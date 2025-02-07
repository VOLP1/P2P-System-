# tracker.py
import socket
import threading
import json
import time
import logging
from dataclasses import dataclass
from typing import Dict, Set

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class PeerInfo:
    """Informações de um peer conectado"""
    id: str
    ip: str
    porta: int
    arquivos: Set[str]
    ultima_vez_visto: float = 0.0
    bytes_enviados: int = 0     # Total de bytes enviados
    bytes_recebidos: int = 0    # Total de bytes recebidos
    tempo_online: float = 0.0   # Tempo total online
    pontuacao: float = 0.0      # Pontuação do peer

class Tracker:
    def __init__(self, host: str = "localhost", porta: int = 55555):
        self.host = host
        self.porta = porta
        self.peers: Dict[str, PeerInfo] = {}
        self.lock = threading.Lock()
        
        # Inicializa socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((host, porta))

    def iniciar(self):
        """Inicia o servidor tracker"""
        self.socket.listen(5)
        logging.info(f"Tracker iniciado em {self.host}:{self.porta}")
        
        # Inicia thread de limpeza
        thread_limpeza = threading.Thread(target=self._limpar_peers_inativos)
        thread_limpeza.daemon = True
        thread_limpeza.start()
        
        # Aceita conexões
        while True:
            try:
                sock, addr = self.socket.accept()
                thread = threading.Thread(target=self._processar_conexao, args=(sock, addr))
                thread.daemon = True
                thread.start()
            except Exception as e:
                logging.error(f"Erro ao aceitar conexão: {e}")

    def _processar_conexao(self, sock: socket.socket, addr: tuple):
        """Processa uma conexão de um peer"""
        try:
            while True:
                dados = sock.recv(1024)
                if not dados:
                    break
                    
                mensagem = json.loads(dados.decode())
                logging.debug(f"Mensagem recebida de {addr}: {mensagem}")
                resposta = self._processar_mensagem(mensagem, addr)
                logging.debug(f"Enviando resposta: {resposta}")
                sock.send(json.dumps(resposta).encode())
                
        except Exception as e:
            logging.error(f"Erro ao processar conexão de {addr}: {e}")
        finally:
            sock.close()

    def _processar_mensagem(self, mensagem: dict, addr: tuple) -> dict:
        """Processa uma mensagem recebida"""
        comando = mensagem.get("comando")
        logging.debug(f"Processando comando: {comando} de {addr}")
        
        if comando == "registrar":
            return self._registrar_peer(mensagem, addr)
        elif comando == "atualizar":
            return self._atualizar_peer(mensagem)
        elif comando == "info_peer":
            return self._info_peer(mensagem)
        elif comando == "buscar":
            return self._buscar_arquivos(mensagem)
        elif comando == "heartbeat":
            return self._processar_heartbeat(mensagem)
        elif comando == "metricas":
            return self._atualizar_metricas(mensagem)
        else:
            return {"status": "erro", "mensagem": "Comando desconhecido"}

    def _registrar_peer(self, mensagem: dict, addr: tuple) -> dict:
        """Registra um novo peer"""
        with self.lock:
            peer_id = f"peer_{len(self.peers) + 1}"
            ip = "127.0.0.1" if addr[0] in ["localhost", "127.0.0.1"] else addr[0]
            porta = mensagem.get("porta")
            
            if not porta:
                return {"status": "erro", "mensagem": "Porta não especificada"}
            
            self.peers[peer_id] = PeerInfo(
                id=peer_id,
                ip=ip,
                porta=porta,
                arquivos=set(mensagem.get("arquivos", [])),
                ultima_vez_visto=time.time()
            )
            
            logging.info(f"Novo peer registrado: {peer_id} em {ip}:{porta}")
            return {
                "status": "sucesso",
                "peer_id": peer_id,
                "mensagem": "Registro realizado com sucesso"
            }

    def _atualizar_peer(self, mensagem: dict) -> dict:
        """Atualiza informações de um peer"""
        with self.lock:
            peer_id = mensagem.get("peer_id")
            if peer_id not in self.peers:
                return {"status": "erro", "mensagem": "Peer não encontrado"}
                
            peer = self.peers[peer_id]
            if "arquivos" in mensagem:
                peer.arquivos = set(mensagem["arquivos"])
            
            peer.ultima_vez_visto = time.time()
            self._atualizar_pontuacao(peer_id)
            return {"status": "sucesso", "mensagem": "Peer atualizado"}

    def _info_peer(self, mensagem: dict) -> dict:
        """Retorna informações de um peer"""
        with self.lock:
            peer_id = mensagem.get("peer_id")
            if not peer_id or peer_id not in self.peers:
                return {"status": "erro", "mensagem": "Peer não encontrado"}
                
            peer = self.peers[peer_id]
            return {
                "status": "sucesso",
                "ip": peer.ip,
                "porta": peer.porta,
                "pontuacao": peer.pontuacao
            }

    def _buscar_arquivos(self, mensagem: dict) -> dict:
        """Busca arquivos na rede P2P"""
        termo = mensagem.get("termo", "").lower()
        logging.debug(f"Buscando arquivos com termo: '{termo}'")
        
        resultados = {}
        try:
            with self.lock:
                for peer_id, peer in self.peers.items():
                    logging.debug(f"Verificando arquivos do peer {peer_id}: {peer.arquivos}")
                    
                    for arquivo in peer.arquivos:
                        if termo in arquivo.lower():
                            if arquivo not in resultados:
                                resultados[arquivo] = []
                            resultados[arquivo].append({
                                "peer_id": peer_id,
                                "ip": peer.ip,
                                "porta": peer.porta,
                                "pontuacao": peer.pontuacao
                            })
                            logging.debug(f"Arquivo encontrado: {arquivo} no peer {peer_id}")
                
                logging.info(f"Busca por '{termo}' encontrou {len(resultados)} arquivos")
                return {
                    "status": "sucesso",
                    "resultados": resultados
                }
                
        except Exception as e:
            logging.error(f"Erro ao buscar arquivos: {str(e)}")
            return {"status": "erro", "mensagem": f"Erro na busca: {str(e)}"}

    def _processar_heartbeat(self, mensagem: dict) -> dict:
        """Processa heartbeat de um peer"""
        with self.lock:
            peer_id = mensagem.get("peer_id")
            if peer_id not in self.peers:
                return {"status": "erro", "mensagem": "Peer não encontrado"}
                
            peer = self.peers[peer_id]
            agora = time.time()
            
            # Atualiza tempo online
            if peer.ultima_vez_visto > 0:
                peer.tempo_online += agora - peer.ultima_vez_visto
            peer.ultima_vez_visto = agora
            
            self._atualizar_pontuacao(peer_id)
            return {"status": "sucesso", "mensagem": "Heartbeat recebido"}

    def _atualizar_metricas(self, mensagem: dict) -> dict:
        """Atualiza métricas de um peer"""
        peer_id = mensagem.get("peer_id")
        tipo = mensagem.get("tipo")
        bytes_total = mensagem.get("bytes", 0)
        
        with self.lock:
            if peer_id not in self.peers:
                return {"status": "erro", "mensagem": "Peer não encontrado"}
                
            peer = self.peers[peer_id]
            
            if tipo == "upload":
                peer.bytes_enviados += bytes_total
            elif tipo == "download":
                peer.bytes_recebidos += bytes_total
                
            self._atualizar_pontuacao(peer_id)
            return {"status": "sucesso", "mensagem": "Métricas atualizadas"}

    def _atualizar_pontuacao(self, peer_id: str):
        """Atualiza a pontuação de um peer"""
        peer = self.peers[peer_id]
        
        # Fatores de pontuação
        PESO_ARQUIVOS = 0.3    # 30% baseado no número de arquivos
        PESO_UPLOAD = 0.4      # 40% baseado no volume de upload
        PESO_TEMPO = 0.3       # 30% baseado no tempo online
        
        # Cálculo dos componentes
        pontos_arquivos = min(1.0, len(peer.arquivos) / 10)  # Máximo com 10 arquivos
        pontos_upload = min(1.0, peer.bytes_enviados / (100 * 1024 * 1024))  # Máximo com 100MB
        pontos_tempo = min(1.0, peer.tempo_online / (24 * 3600))  # Máximo com 24 horas
        
        # Pontuação final (0.0 a 1.0)
        peer.pontuacao = (
            pontos_arquivos * PESO_ARQUIVOS +
            pontos_upload * PESO_UPLOAD +
            pontos_tempo * PESO_TEMPO
        )
        
        logging.debug(f"Pontuação atualizada para {peer_id}: {peer.pontuacao:.2f}")

    def _limpar_peers_inativos(self):
        """Remove peers inativos"""
        while True:
            time.sleep(60)
            with self.lock:
                tempo_atual = time.time()
                inativos = [
                    peer_id for peer_id, peer in self.peers.items()
                    if tempo_atual - peer.ultima_vez_visto > 120
                ]
                for peer_id in inativos:
                    del self.peers[peer_id]
                    logging.info(f"Peer removido por inatividade: {peer_id}")

if __name__ == "__main__":
    tracker = Tracker()
    try:
        tracker.iniciar()
    except KeyboardInterrupt:
        print("\nTracker encerrado pelo usuário")