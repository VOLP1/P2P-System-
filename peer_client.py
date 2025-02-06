import socket
import threading
import json
import time
import os
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, BinaryIO

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@dataclass
class ArquivoInfo:
    """Informações sobre um arquivo compartilhado"""
    nome: str
    tamanho: int
    hash: str
    partes: int = 1  # Para futuras conexões paralelas

class PeerClient:
    def __init__(self, host: str = "localhost", porta: int = 0, diretorio_compartilhado: str = "compartilhado"):
        """
        Inicializa o cliente peer.
        
        Args:
            host: Endereço IP do peer
            porta: Porta para conexões (0 para automático)
            diretorio_compartilhado: Diretório para arquivos compartilhados
        """
        self.host = host
        self.socket_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_servidor.bind((host, porta))
        self.porta = self.socket_servidor.getsockname()[1]  # Pega a porta atribuída
        
        self.peer_id: Optional[str] = None
        self.arquivos: Dict[str, ArquivoInfo] = {}
        self.peers_conectados: Dict[str, tuple] = {}  # {peer_id: (ip, porta)}
        self.chats_ativos: Dict[str, socket.socket] = {}  # {peer_id: socket}
        self.diretorio_compartilhado = diretorio_compartilhado
        
        # Certifica que o diretório existe
        if not os.path.exists(diretorio_compartilhado):
            os.makedirs(diretorio_compartilhado)
        
        # Inicializa socket servidor
        self.socket_servidor.listen(10)
        self.thread_servidor = threading.Thread(target=self._aceitar_conexoes)
        self.thread_servidor.daemon = True
        self.thread_servidor.start()

    def adicionar_arquivo(self, caminho_arquivo: str) -> ArquivoInfo:
        """
        Adiciona um arquivo para compartilhamento.
        
        Args:
            caminho_arquivo: Caminho do arquivo a ser compartilhado
            
        Returns:
            ArquivoInfo com metadados do arquivo
        """
        # Certifica que o diretório existe
        if not os.path.exists(self.diretorio_compartilhado):
            os.makedirs(self.diretorio_compartilhado)
            
        nome_arquivo = os.path.basename(caminho_arquivo)
        destino = os.path.join(self.diretorio_compartilhado, nome_arquivo)
        
        # Se o arquivo não estiver no diretório compartilhado, copia
        if os.path.abspath(caminho_arquivo) != os.path.abspath(destino):
            with open(caminho_arquivo, 'rb') as origem, open(destino, 'wb') as dest:
                dest.write(origem.read())
        
        # Calcula informações do arquivo
        tamanho = os.path.getsize(destino)
        with open(destino, 'rb') as f:
            hash_arquivo = self._calcular_hash_arquivo(f)
        
        info = ArquivoInfo(nome_arquivo, tamanho, hash_arquivo)
        self.arquivos[nome_arquivo] = info
        
        # Atualiza lista no tracker
        self._atualizar_arquivos_no_tracker()
        
        return info

    def conectar_tracker(self, tracker_host: str, tracker_porta: int) -> bool:
        """
        Conecta ao servidor tracker e registra o peer.
        
        Returns:
            bool: True se conexão foi bem sucedida
        """
        try:
            self.socket_tracker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket_tracker.connect((tracker_host, tracker_porta))
            
            # Registra no tracker
            mensagem = {
                "comando": "registrar",
                "porta": self.porta,
                "arquivos": list(self.arquivos.keys())
            }
            
            self.socket_tracker.send(json.dumps(mensagem).encode('utf-8'))
            resposta = json.loads(self.socket_tracker.recv(1024).decode('utf-8'))
            
            if resposta["status"] == "sucesso":
                self.peer_id = resposta["peer_id"]
                logging.info(f"Conectado ao tracker como {self.peer_id}")
                
                # Inicia thread de heartbeat
                self.thread_heartbeat = threading.Thread(target=self._enviar_heartbeat)
                self.thread_heartbeat.daemon = True
                self.thread_heartbeat.start()
                
                return True
            return False
            
        except Exception as e:
            logging.error(f"Erro ao conectar ao tracker: {str(e)}")
            return False

    def iniciar_chat(self, peer_id: str) -> bool:
        """
        Inicia uma sessão de chat com outro peer.
        """
        if peer_id in self.chats_ativos:
            return True
            
        try:
            # Prepara mensagem para o tracker
            mensagem = {
                "comando": "info_peer",
                "peer_id": peer_id
            }
            
            # Envia mensagem e aguarda resposta
            self.socket_tracker.send(json.dumps(mensagem).encode('utf-8'))
            dados_resposta = self.socket_tracker.recv(1024).decode('utf-8')
            logging.debug(f"Resposta raw do tracker: {dados_resposta}")
            
            # Processa resposta
            try:
                resposta = json.loads(dados_resposta)
                logging.debug(f"Resposta decodificada: {resposta}")
            except json.JSONDecodeError as e:
                logging.error(f"Erro ao decodificar resposta do tracker: {e}")
                return False
                
            # Verifica status da resposta
            if resposta.get("status") != "sucesso":
                logging.error(f"Erro na resposta do tracker: {resposta.get('mensagem', 'Erro desconhecido')}")
                return False
                
            # Extrai informações do peer
            ip = resposta.get("ip")
            porta = resposta.get("porta")
            
            if not ip or not porta:
                logging.error(f"Informações incompletas do peer: ip={ip}, porta={porta}")
                return False
                
            logging.info(f"Tentando conectar ao peer {peer_id} em {ip}:{porta}")
            
            # Estabelece conexão
            socket_chat = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socket_chat.settimeout(5)
            socket_chat.connect((ip, porta))
            socket_chat.settimeout(None)
            
            # Envia identificação
            mensagem_id = {
                "tipo": "chat",
                "peer_id": self.peer_id
            }
            socket_chat.send(json.dumps(mensagem_id).encode('utf-8'))
            
            # Registra chat ativo
            self.chats_ativos[peer_id] = socket_chat
            
            # Inicia thread de recebimento
            thread_chat = threading.Thread(
                target=self._receber_mensagens_chat,
                args=(peer_id, socket_chat)
            )
            thread_chat.daemon = True
            thread_chat.start()
            
            logging.info(f"Conexão estabelecida com sucesso com o peer {peer_id}")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao iniciar chat com {peer_id}: {str(e)}")
            logging.debug(f"Detalhes do erro:", exc_info=True)
            return False
                
    def enviar_mensagem_chat(self, peer_id: str, mensagem: str):
        """
        Envia mensagem para um peer específico.
        
        Args:
            peer_id: ID do peer destinatário
            mensagem: Conteúdo da mensagem
        """
        if peer_id not in self.chats_ativos:
            if not self.iniciar_chat(peer_id):
                return False
                
        try:
            dados = {
                "tipo": "mensagem",
                "conteudo": mensagem
            }
            self.chats_ativos[peer_id].send(json.dumps(dados).encode('utf-8'))
            return True
        except Exception as e:
            logging.error(f"Erro ao enviar mensagem: {str(e)}")
            return False

    def solicitar_arquivo(self, peer_id: str, nome_arquivo: str) -> bool:
        """
        Solicita download de um arquivo de outro peer.
        
        Args:
            peer_id: ID do peer que possui o arquivo
            nome_arquivo: Nome do arquivo desejado
        
        Returns:
            bool: True se download foi iniciado com sucesso
        """
        if peer_id not in self.chats_ativos:
            if not self.iniciar_chat(peer_id):
                return False
        
        try:
            socket_transfer = self.chats_ativos[peer_id]
            
            # Envia solicitação de arquivo
            mensagem = {
                "tipo": "arquivo_solicitacao",
                "nome": nome_arquivo
            }
            socket_transfer.send(json.dumps(mensagem).encode('utf-8'))
            
            # Inicia thread para receber o arquivo
            thread_download = threading.Thread(
                target=self._receber_arquivo,
                args=(socket_transfer, nome_arquivo)
            )
            thread_download.daemon = True
            thread_download.start()
            
            return True
            
        except Exception as e:
            logging.error(f"Erro ao solicitar arquivo: {str(e)}")
            return False

    def _calcular_hash_arquivo(self, arquivo: BinaryIO) -> str:
        """Calcula o hash SHA-256 de um arquivo"""
        sha256 = hashlib.sha256()
        for bloco in iter(lambda: arquivo.read(4096), b''):
            sha256.update(bloco)
        arquivo.seek(0)  # Volta para o início do arquivo
        return sha256.hexdigest()

    def _atualizar_arquivos_no_tracker(self):
        """Atualiza a lista de arquivos no tracker"""
        if hasattr(self, 'socket_tracker') and self.peer_id:
            mensagem = {
                "comando": "atualizar",
                "peer_id": self.peer_id,
                "arquivos": list(self.arquivos.keys())
            }
            self.socket_tracker.send(json.dumps(mensagem).encode('utf-8'))

    def _aceitar_conexoes(self):
        """Thread que aceita conexões de outros peers"""
        while True:
            try:
                socket_cliente, endereco = self.socket_servidor.accept()
                thread_cliente = threading.Thread(
                    target=self._gerenciar_conexao_cliente,
                    args=(socket_cliente, endereco)
                )
                thread_cliente.daemon = True
                thread_cliente.start()
            except Exception as e:
                logging.error(f"Erro ao aceitar conexão: {str(e)}")

    def _gerenciar_conexao_cliente(self, socket_cliente: socket.socket, endereco: tuple):
        """Gerencia uma conexão recebida de outro peer"""
        try:
            dados = socket_cliente.recv(1024).decode('utf-8')
            mensagem = json.loads(dados)
            
            if mensagem["tipo"] == "chat":
                # Aceita conexão de chat
                peer_id = mensagem["peer_id"]
                self.chats_ativos[peer_id] = socket_cliente
                self._receber_mensagens_chat(peer_id, socket_cliente)
                
            elif mensagem["tipo"] == "arquivo_solicitacao":
                # Processa solicitação de arquivo
                nome_arquivo = mensagem["nome"]
                if nome_arquivo in self.arquivos:
                    self._enviar_arquivo(socket_cliente, nome_arquivo)
                else:
                    socket_cliente.send(json.dumps({
                        "tipo": "erro",
                        "mensagem": "Arquivo não encontrado"
                    }).encode('utf-8'))
                
        except Exception as e:
            logging.error(f"Erro ao gerenciar conexão: {str(e)}")
            socket_cliente.close()

    def _receber_mensagens_chat(self, peer_id: str, socket_chat: socket.socket):
        """Thread que recebe mensagens de um chat específico"""
        try:
            while True:
                dados = socket_chat.recv(1024).decode('utf-8')
                if not dados:
                    break
                    
                mensagem = json.loads(dados)
                if mensagem["tipo"] == "mensagem":
                    # Aqui você pode implementar um callback para a interface
                    print(f"Mensagem de {peer_id}: {mensagem['conteudo']}")
                    
        except Exception as e:
            logging.error(f"Erro ao receber mensagens de {peer_id}: {str(e)}")
        finally:
            socket_chat.close()
            if peer_id in self.chats_ativos:
                del self.chats_ativos[peer_id]

    def _enviar_heartbeat(self):
        """Thread que envia heartbeat periódico para o tracker"""
        while True:
            try:
                mensagem = {
                    "comando": "heartbeat",
                    "peer_id": self.peer_id
                }
                self.socket_tracker.send(json.dumps(mensagem).encode('utf-8'))
                time.sleep(60)  # Heartbeat a cada minuto
            except Exception as e:
                logging.error(f"Erro ao enviar heartbeat: {str(e)}")
                break

    def _receber_arquivo(self, socket_origem: socket.socket, nome_arquivo: str):
        """Recebe um arquivo de outro peer"""
        try:
            # Recebe metadados
            dados = socket_origem.recv(1024).decode('utf-8')
            metadados = json.loads(dados)
            
            if metadados["tipo"] != "arquivo_metadados":
                raise Exception("Resposta inválida do peer")
                
            tamanho = metadados["tamanho"]
            hash_esperado = metadados["hash"]
            
            # Prepara para receber o arquivo
            caminho = os.path.join(self.diretorio_compartilhado, nome_arquivo)
            bytes_recebidos = 0
            
            with open(caminho, 'wb') as arquivo:
                while bytes_recebidos < tamanho:
                    bloco = socket_origem.recv(min(4096, tamanho - bytes_recebidos))
                    if not bloco:
                        raise Exception("Conexão interrompida")
                    arquivo.write(bloco)
                    bytes_recebidos += len(bloco)
            
            # Verifica hash
            with open(caminho, 'rb') as arquivo:
                hash_recebido = self._calcular_hash_arquivo(arquivo)
                
            if hash_recebido != hash_esperado:
                os.remove(caminho)
                raise Exception("Hash do arquivo não corresponde")
            
            logging.info(f"Arquivo {nome_arquivo} recebido com sucesso")
            
        except Exception as e:
            logging.error(f"Erro ao receber arquivo: {str(e)}")
            if 'caminho' in locals() and os.path.exists(caminho):
                os.remove(caminho)

    def _enviar_arquivo(self, socket_cliente: socket.socket, nome_arquivo: str):
        """Envia um arquivo para outro peer"""
        try:
            caminho = os.path.join(self.diretorio_compartilhado, nome_arquivo)
            info_arquivo = self.arquivos[nome_arquivo]
            
            # Envia metadados
            metadados = {
                "tipo": "arquivo_metadados",
                "nome": nome_arquivo,
                "tamanho": info_arquivo.tamanho,
                "hash": info_arquivo.hash
            }
            socket_cliente.send(json.dumps(metadados).encode('utf-8'))
            
            # Envia o arquivo
            with open(caminho, 'rb') as arquivo:
                while True:
                    bloco = arquivo.read(4096)
                    if not bloco:
                        break
                    socket_cliente.send(bloco)
            
            logging.info(f"Arquivo {nome_arquivo} enviado com sucesso")
            
        except Exception as e:
            logging.error(f"Erro ao enviar arquivo: {str(e)}")
            try:
                socket_cliente.send(json.dumps({
                    "tipo": "erro",
                    "mensagem": str(e)
                }).encode('utf-8'))
            except:
                pass