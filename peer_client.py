# peer_client.py
import socket
import threading
import json
import time
import os
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, Optional

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class ArquivoInfo:
    """Informações sobre um arquivo compartilhado"""
    nome: str
    tamanho: int
    hash: str

class PeerClient:
    def __init__(self, host: str = "localhost", porta: int = 0, diretorio: str = "compartilhado"):
        self.host = host
        self.porta = porta
        self.diretorio = diretorio
        self.peer_id = None
        self.arquivos: Dict[str, ArquivoInfo] = {}
        self.chats_ativos: Dict[str, socket.socket] = {}
        
        # Garante que o diretório existe
        os.makedirs(diretorio, exist_ok=True)
        
        # Inicializa socket servidor
        self.socket_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_servidor.bind((host, porta))
        self.porta = self.socket_servidor.getsockname()[1]
        
        # Inicia thread do servidor
        self.socket_servidor.listen(5)
        self.thread_servidor = threading.Thread(target=self._aceitar_conexoes)
        self.thread_servidor.daemon = True
        self.thread_servidor.start()
        
        logging.info(f"Peer iniciado em {host}:{self.porta}")

    def _enviar_comando_tracker(self, comando: dict) -> dict:
        """Envia um comando para o tracker e recebe a resposta"""
        try:
            # Limpa buffer do socket
            self.socket_tracker.settimeout(0.1)
            try:
                while self.socket_tracker.recv(1024):
                    pass
            except:
                pass
            finally:
                self.socket_tracker.settimeout(None)
            
            # Envia comando
            self.socket_tracker.send(json.dumps(comando).encode())
            resposta = json.loads(self.socket_tracker.recv(1024).decode())
            logging.debug(f"Resposta do tracker para {comando['comando']}: {resposta}")
            return resposta
        except Exception as e:
            logging.error(f"Erro ao enviar comando ao tracker: {e}")
            return {"status": "erro", "mensagem": str(e)}

    def conectar_tracker(self, host: str, porta: int) -> bool:
        """Conecta ao tracker e registra o peer"""
        try:
            self.socket_tracker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket_tracker.connect((host, porta))
            
            mensagem = {
                "comando": "registrar",
                "porta": self.porta,
                "arquivos": list(self.arquivos.keys())
            }
            
            self.socket_tracker.send(json.dumps(mensagem).encode())
            resposta = json.loads(self.socket_tracker.recv(1024).decode())
            
            if resposta["status"] == "sucesso":
                self.peer_id = resposta["peer_id"]
                logging.info(f"Registrado no tracker como {self.peer_id}")
                
                # Inicia heartbeat
                self.thread_heartbeat = threading.Thread(target=self._enviar_heartbeat)
                self.thread_heartbeat.daemon = True
                self.thread_heartbeat.start()
                
                return True
            return False
            
        except Exception as e:
            logging.error(f"Erro ao conectar ao tracker: {e}")
            return False

    def adicionar_arquivo(self, caminho: str) -> ArquivoInfo:
        """Adiciona um arquivo para compartilhamento"""
        nome = os.path.basename(caminho)
        destino = os.path.join(self.diretorio, nome)
        
        # Copia arquivo se necessário
        if os.path.abspath(caminho) != os.path.abspath(destino):
            with open(caminho, 'rb') as origem, open(destino, 'wb') as dest:
                dest.write(origem.read())
        
        # Calcula informações
        tamanho = os.path.getsize(destino)
        with open(destino, 'rb') as f:
            hash_obj = hashlib.sha256()
            for bloco in iter(lambda: f.read(4096), b''):
                hash_obj.update(bloco)
            hash_arquivo = hash_obj.hexdigest()
        
        # Registra arquivo
        info = ArquivoInfo(nome, tamanho, hash_arquivo)
        self.arquivos[nome] = info
        
        # Atualiza tracker
        if hasattr(self, 'socket_tracker'):
            self._enviar_comando_tracker({
                "comando": "atualizar",
                "peer_id": self.peer_id,
                "arquivos": list(self.arquivos.keys())
            })
        
        return info

    def buscar_arquivos(self, termo: str) -> dict:
        """Busca arquivos na rede pelo termo"""
        try:
            logging.info(f"Iniciando busca por '{termo}'")
            resposta = self._enviar_comando_tracker({
                "comando": "buscar",
                "termo": termo
            })
            
            if resposta["status"] != "sucesso":
                logging.error(f"Erro na resposta do tracker: {resposta}")
                return {}
            
            resultados = resposta.get("resultados", {})
            for arquivo, peers in resultados.items():
                logging.info(f"Arquivo: {arquivo}")
                for peer in peers:
                    logging.info(f"  - Disponível no peer {peer['peer_id']} ({peer['ip']}:{peer['porta']})")
            
            return resultados
            
        except Exception as e:
            logging.error(f"Erro ao buscar arquivos: {str(e)}")
            return {}

    def iniciar_chat(self, peer_id: str) -> bool:
        """Inicia uma sessão de chat com outro peer"""
        if peer_id in self.chats_ativos:
            return True
            
        try:
            # Obtém informações do peer
            resposta = self._enviar_comando_tracker({
                "comando": "info_peer",
                "peer_id": peer_id
            })
            
            if resposta["status"] != "sucesso":
                logging.error(f"Erro na resposta do tracker: {resposta}")
                return False
            
            # Extrai informações
            ip = resposta.get("ip")
            porta = resposta.get("porta")
            logging.debug(f"Informações recebidas - IP: {ip}, Porta: {porta}")
            
            if not ip or not porta:
                logging.error("IP ou porta não recebidos do tracker")
                return False
            
            # Conecta ao peer
            logging.info(f"Tentando conectar a {ip}:{porta}")
            socket_chat = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socket_chat.connect((ip, porta))
            
            # Envia identificação
            mensagem = {
                "tipo": "chat",
                "peer_id": self.peer_id
            }
            socket_chat.send(json.dumps(mensagem).encode())
            
            # Registra conexão
            self.chats_ativos[peer_id] = socket_chat
            
            # Inicia thread de recebimento
            thread = threading.Thread(target=self._receber_mensagens_chat, args=(peer_id, socket_chat))
            thread.daemon = True
            thread.start()
            
            logging.info(f"Chat iniciado com {peer_id}")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao iniciar chat: {str(e)}")
            logging.debug("Erro detalhado:", exc_info=True)
            return False

    def enviar_mensagem_chat(self, peer_id: str, mensagem: str) -> bool:
        """Envia mensagem para outro peer"""
        if peer_id not in self.chats_ativos:
            if not self.iniciar_chat(peer_id):
                return False
        
        try:
            dados = {
                "tipo": "mensagem",
                "conteudo": mensagem
            }
            self.chats_ativos[peer_id].send(json.dumps(dados).encode())
            return True
        except Exception as e:
            logging.error(f"Erro ao enviar mensagem: {e}")
            if peer_id in self.chats_ativos:
                del self.chats_ativos[peer_id]
            return False

    def solicitar_arquivo(self, peer_id: str, nome_arquivo: str) -> bool:
        """Solicita download de arquivo de outro peer"""
        try:
            # Obtém informações do peer
            resposta = self._enviar_comando_tracker({
                "comando": "info_peer",
                "peer_id": peer_id
            })
            
            if resposta["status"] != "sucesso":
                return False
            
            ip = resposta.get("ip")
            porta = resposta.get("porta")
            
            if not ip or not porta:
                logging.error("IP ou porta não recebidos do tracker")
                return False
            
            # Estabelece conexão para transferência
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((ip, porta))
            
            # Solicita arquivo
            mensagem = {
                "tipo": "arquivo",
                "nome": nome_arquivo
            }
            sock.send(json.dumps(mensagem).encode())
            
            # Recebe arquivo
            caminho = os.path.join(self.diretorio, nome_arquivo)
            with open(caminho, 'wb') as f:
                while True:
                    dados = sock.recv(4096)
                    if not dados:
                        break
                    f.write(dados)
            
            logging.info(f"Arquivo {nome_arquivo} recebido com sucesso")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao solicitar arquivo: {e}")
            if 'caminho' in locals() and os.path.exists(caminho):
                os.remove(caminho)
            return False

    def _aceitar_conexoes(self):
        """Thread que aceita conexões de outros peers"""
        while True:
            try:
                sock, addr = self.socket_servidor.accept()
                thread = threading.Thread(target=self._processar_conexao, args=(sock,))
                thread.daemon = True
                thread.start()
            except Exception as e:
                logging.error(f"Erro ao aceitar conexão: {e}")

    def _processar_conexao(self, sock: socket.socket):
        """Processa uma nova conexão"""
        try:
            dados = sock.recv(1024)
            if not dados:
                return
                
            mensagem = json.loads(dados.decode())
            tipo = mensagem.get("tipo")
            
            if tipo == "chat":
                # Aceita conexão de chat
                peer_id = mensagem["peer_id"]
                self.chats_ativos[peer_id] = sock
                self._receber_mensagens_chat(peer_id, sock)
                
            elif tipo == "arquivo":
                # Envia arquivo solicitado
                nome = mensagem["nome"]
                if nome in self.arquivos:
                    caminho = os.path.join(self.diretorio, nome)
                    with open(caminho, 'rb') as f:
                        while True:
                            dados = f.read(4096)
                            if not dados:
                                break
                            sock.send(dados)
                sock.close()
                
        except Exception as e:
            logging.error(f"Erro ao processar conexão: {e}")
            sock.close()

    def _receber_mensagens_chat(self, peer_id: str, sock: socket.socket):
        """Processa mensagens de chat"""
        try:
            while True:
                dados = sock.recv(1024)
                if not dados:
                    break
                    
                mensagem = json.loads(dados.decode())
                if mensagem["tipo"] == "mensagem":
                    print(f"Mensagem de {peer_id}: {mensagem['conteudo']}")
                    
        except Exception as e:
            logging.error(f"Erro ao receber mensagens de {peer_id}: {e}")
        finally:
            if peer_id in self.chats_ativos:
                del self.chats_ativos[peer_id]
            sock.close()

    def _enviar_heartbeat(self):
        """Envia heartbeat periódico para o tracker"""
        while True:
            try:
                mensagem = {
                    "comando": "heartbeat",
                    "peer_id": self.peer_id
                }
                self.socket_tracker.send(json.dumps(mensagem).encode())
                time.sleep(30)
            except:
                break

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Cliente P2P')
    parser.add_argument('--host', default='localhost', help='Host do tracker')
    parser.add_argument('--porta', type=int, default=55555, help='Porta do tracker')
    parser.add_argument('--diretorio', default='compartilhado', help='Diretório de arquivos')
    args = parser.parse_args()
    
    cliente = PeerClient(diretorio=args.diretorio)
    if cliente.conectar_tracker(args.host, args.porta):
        print(f"Conectado como {cliente.peer_id}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nEncerrando cliente...")