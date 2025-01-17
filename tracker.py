"""
Sistema de Tracker P2P
=====================

Este módulo implementa um servidor tracker centralizado para um sistema P2P (Peer-to-Peer).
O tracker é responsável por gerenciar a rede P2P, mantendo registro dos peers ativos,
arquivos compartilhados e métricas de colaboração.

Funcionalidades Principais:
--------------------------
1. Gerenciamento de Peers:
   - Registro e remoção de peers
   - Monitoramento de status dos peers
   - Atualização de informações

2. Gerenciamento de Arquivos:
   - Indexação de arquivos compartilhados
   - Sistema de busca
   - Rastreamento de localização de arquivos

3. Sistema de Incentivo:
   - Cálculo de pontuação colaborativa
   - Métricas de participação
   - Priorização de peers ativos

Autor: Eduardo Volpi, Pedro Brazil
"""

import socket
import threading
import json
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Set
import logging

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@dataclass
class InfoPeer:
    """
    Classe para armazenar informações de cada peer na rede.
    
    Atributos:
        id (str): Identificador único do peer
        ip (str): Endereço IP do peer
        porta (int): Porta de conexão do peer
        arquivos (List[str]): Lista de arquivos compartilhados
        volume_upload (float): Volume total de dados enviados em MB
        tempo_conexao (float): Tempo total de conexão em segundos
        ultima_vez_visto (float): Timestamp da última atividade
        pontuacao_colaborativa (float): Métrica de colaboração do peer
    """
    id: str
    ip: str
    porta: int
    arquivos: List[str]
    volume_upload: float = 0.0
    tempo_conexao: float = 0.0
    ultima_vez_visto: float = 0.0
    pontuacao_colaborativa: float = 0.0

class Tracker:
    """
    Implementação do servidor tracker centralizado.
    
    Esta classe gerencia toda a lógica do tracker, incluindo conexões,
    registro de peers, busca de arquivos e sistema de incentivo.
    """

    def __init__(self, host: str = "localhost", porta: int = 55555):
        """
        Inicializa o servidor tracker.

        Args:
            host (str): Endereço IP do servidor
            porta (int): Porta para conexões
        """
        self.host = host
        self.porta = porta
        self.peers: Dict[str, InfoPeer] = {}
        self.indice_arquivos: Dict[str, Set[str]] = {}
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.trava = threading.Lock()

    def iniciar(self):
        """
        Inicia o servidor tracker e começa a aceitar conexões.
        Também inicia a thread de limpeza de peers inativos.
        """
        self.socket.bind((self.host, self.porta))
        self.socket.listen(100)
        logging.info(f"Tracker iniciado em {self.host}:{self.porta}")

        thread_limpeza = threading.Thread(target=self._limpar_peers_inativos)
        thread_limpeza.daemon = True
        thread_limpeza.start()

        while True:
            socket_cliente, endereco = self.socket.accept()
            thread_cliente = threading.Thread(
                target=self._gerenciar_cliente,
                args=(socket_cliente, endereco)
            )
            thread_cliente.daemon = True
            thread_cliente.start()

    def _gerenciar_cliente(self, socket_cliente: socket.socket, endereco: tuple):
        """
        Gerencia a comunicação com um cliente conectado.

        Args:
            socket_cliente: Socket do cliente conectado
            endereco: Tupla (ip, porta) do cliente
        """
        try:
            while True:
                dados = socket_cliente.recv(4096).decode('utf-8')
                if not dados:
                    break

                try:
                    mensagem = json.loads(dados)
                    resposta = self._processar_mensagem(mensagem, endereco)
                    socket_cliente.send(json.dumps(resposta).encode('utf-8'))
                except json.JSONDecodeError:
                    logging.error(f"Erro ao decodificar mensagem: {dados}")
                    socket_cliente.send(json.dumps({
                        "status": "erro",
                        "mensagem": "Formato JSON inválido"
                    }).encode('utf-8'))

        except Exception as e:
            logging.error(f"Erro na conexão com {endereco}: {str(e)}")
        finally:
            socket_cliente.close()

    def _processar_mensagem(self, mensagem: dict, endereco: tuple) -> dict:
        """
        Processa as mensagens recebidas dos peers.

        Args:
            mensagem: Dicionário com o comando e dados da mensagem
            endereco: Tupla (ip, porta) do remetente

        Returns:
            dict: Resposta ao comando recebido
        """
        comando = mensagem.get("comando")
        
        if comando == "registrar":
            return self._registrar_peer(mensagem, endereco)
        elif comando == "atualizar":
            return self._atualizar_peer(mensagem)
        elif comando == "buscar":
            return self._buscar_arquivos(mensagem)
        elif comando == "info_peer":
            return self._obter_info_peer(mensagem)
        elif comando == "heartbeat":
            return self._processar_heartbeat(mensagem)
        else:
            return {"status": "erro", "mensagem": "Comando desconhecido"}

    def _registrar_peer(self, mensagem: dict, endereco: tuple) -> dict:
        """
        Registra um novo peer no sistema.

        Args:
            mensagem: Dados do peer para registro
            endereco: Endereço do peer

        Returns:
            dict: Confirmação do registro
        """
        with self.trava:
            peer_id = mensagem.get("peer_id")
            if not peer_id:
                peer_id = f"peer_{len(self.peers) + 1}"

            info_peer = InfoPeer(
                id=peer_id,
                ip=endereco[0],
                porta=mensagem.get("porta"),
                arquivos=mensagem.get("arquivos", []),
                tempo_conexao=time.time(),
                ultima_vez_visto=time.time()
            )

            self.peers[peer_id] = info_peer
            self._atualizar_indice_arquivos(peer_id, info_peer.arquivos)

            logging.info(f"Novo peer registrado: {peer_id}")
            return {
                "status": "sucesso",
                "peer_id": peer_id,
                "mensagem": "Registro realizado com sucesso"
            }

    def _atualizar_peer(self, mensagem: dict) -> dict:
        """
        Atualiza as informações de um peer existente.

        Args:
            mensagem: Dicionário contendo as novas informações do peer

        Returns:
            dict: Confirmação da atualização
        """
        with self.trava:
            peer_id = mensagem.get("peer_id")
            if peer_id not in self.peers:
                return {"status": "erro", "mensagem": "Peer não encontrado"}

            peer = self.peers[peer_id]
            
            if "arquivos" in mensagem:
                self._atualizar_indice_arquivos(peer_id, mensagem["arquivos"])
                peer.arquivos = mensagem["arquivos"]

            if "volume_upload" in mensagem:
                peer.volume_upload = mensagem["volume_upload"]
            
            peer.ultima_vez_visto = time.time()
            self._atualizar_pontuacao_colaborativa(peer_id)

            return {"status": "sucesso", "mensagem": "Peer atualizado"}

    def _buscar_arquivos(self, mensagem: dict) -> dict:
        """
        Realiza busca de arquivos na rede.

        Args:
            mensagem: Dicionário contendo o termo de busca

        Returns:
            dict: Resultados da busca com peers que possuem os arquivos
        """
        termo_busca = mensagem.get("termo", "").lower()
        resultados = {}

        with self.trava:
            for nome_arquivo, peer_ids in self.indice_arquivos.items():
                if termo_busca in nome_arquivo.lower():
                    peers_com_arquivo = []
                    for peer_id in peer_ids:
                        if peer_id in self.peers:
                            peer = self.peers[peer_id]
                            peers_com_arquivo.append({
                                "peer_id": peer_id,
                                "ip": peer.ip,
                                "porta": peer.porta,
                                "pontuacao_colaborativa": peer.pontuacao_colaborativa
                            })
                    
                    if peers_com_arquivo:
                        resultados[nome_arquivo] = sorted(
                            peers_com_arquivo,
                            key=lambda x: x["pontuacao_colaborativa"],
                            reverse=True
                        )

        return {
            "status": "sucesso",
            "resultados": resultados
        }

    def _atualizar_indice_arquivos(self, peer_id: str, arquivos: List[str]):
        """
        Atualiza o índice de arquivos compartilhados.

        Args:
            peer_id: Identificador do peer
            arquivos: Lista de arquivos compartilhados pelo peer
        """
        for arquivo_peers in self.indice_arquivos.values():
            arquivo_peers.discard(peer_id)

        for nome_arquivo in arquivos:
            if nome_arquivo not in self.indice_arquivos:
                self.indice_arquivos[nome_arquivo] = set()
            self.indice_arquivos[nome_arquivo].add(peer_id)

    def _atualizar_pontuacao_colaborativa(self, peer_id: str):
        """
        Calcula e atualiza a pontuação colaborativa de um peer.

        Args:
            peer_id: Identificador do peer
        """
        peer = self.peers[peer_id]
        
        # Cálculo de diferentes aspectos da pontuação
        pontos_tempo_conexao = min(1.0, (time.time() - peer.tempo_conexao) / 3600)
        pontos_volume_upload = min(1.0, peer.volume_upload / 100)
        pontos_arquivos = min(1.0, len(peer.arquivos) / 10)

        # Pontuação final ponderada
        peer.pontuacao_colaborativa = (
            pontos_tempo_conexao * 0.3 +
            pontos_volume_upload * 0.4 +
            pontos_arquivos * 0.3
        )

    def _limpar_peers_inativos(self, tempo_limite: int = 300):
        """
        Remove peers que estão inativos por muito tempo.

        Args:
            tempo_limite: Tempo em segundos após o qual um peer é considerado inativo
        """
        while True:
            time.sleep(60)  # Verifica a cada minuto
            tempo_atual = time.time()
            
            with self.trava:
                peers_inativos = [
                    peer_id for peer_id, peer in self.peers.items()
                    if tempo_atual - peer.ultima_vez_visto > tempo_limite
                ]
                
                for peer_id in peers_inativos:
                    logging.info(f"Removendo peer inativo: {peer_id}")
                    del self.peers[peer_id]
                    for arquivo_peers in self.indice_arquivos.values():
                        arquivo_peers.discard(peer_id)

    def _processar_heartbeat(self, mensagem: dict) -> dict:
        """
        Processa sinais de heartbeat dos peers.

        Args:
            mensagem: Mensagem contendo o ID do peer

        Returns:
            dict: Confirmação do recebimento do heartbeat
        """
        peer_id = mensagem.get("peer_id")
        if peer_id in self.peers:
            self.peers[peer_id].ultima_vez_visto = time.time()
            return {"status": "sucesso", "mensagem": "Heartbeat recebido"}
        return {"status": "erro", "mensagem": "Peer não encontrado"}

    def _obter_info_peer(self, mensagem: dict) -> dict:
        """
        Retorna informações detalhadas sobre um peer específico.

        Args:
            mensagem: Mensagem contendo o ID do peer

        Returns:
            dict: Informações detalhadas do peer
        """
        peer_id = mensagem.get("peer_id")
        if peer_id in self.peers:
            peer = self.peers[peer_id]
            return {
                "status": "sucesso",
                "info_peer": asdict(peer)
            }
        return {"status": "erro", "mensagem": "Peer não encontrado"}

if __name__ == "__main__":
    """
    Ponto de entrada principal do programa.
    Inicia o servidor tracker e mantém ele rodando até ser interrompido.
    """
    tracker = Tracker()
    try:
        print("Iniciando servidor tracker...")
        tracker.iniciar()
    except KeyboardInterrupt:
        logging.info("Tracker encerrado pelo usuário")
    except Exception as e:
        logging.error(f"Erro fatal no tracker: {str(e)}")
