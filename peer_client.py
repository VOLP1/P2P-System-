# peer_client.py
import socket
import threading
import json
import time
import os
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class PartInfo:
    """Informações sobre uma parte do arquivo"""
    numero: int        # Número da parte
    inicio: int       # Posição inicial no arquivo
    tamanho: int      # Tamanho da parte
    hash: str         # Hash da parte para verificação

@dataclass
class ArquivoInfo:
    """Informações sobre um arquivo compartilhado"""
    nome: str
    tamanho: int
    hash: str
    tamanho_parte: int = 1024 * 1024  # 1MB por parte
    partes: List[PartInfo] = None
    
    def __post_init__(self):
        if self.partes is None:
            self.partes = []
            # Calcula número de partes necessárias
            num_partes = (self.tamanho + self.tamanho_parte - 1) // self.tamanho_parte
            posicao = 0
            
            for i in range(num_partes):
                tamanho_atual = min(self.tamanho_parte, self.tamanho - posicao)
                self.partes.append(PartInfo(i, posicao, tamanho_atual, ""))
                posicao += tamanho_atual

    def calcular_hashes(self, caminho: str):
        """Calcula hashes de todas as partes"""
        with open(caminho, 'rb') as f:
            for parte in self.partes:
                f.seek(parte.inicio)
                dados = f.read(parte.tamanho)
                parte.hash = hashlib.sha256(dados).hexdigest()

class GerenciadorTransferencia:
    """Gerencia a transferência paralela de arquivos"""
    def __init__(self, peer_client, max_conexoes: int = 4):
        self.peer_client = peer_client
        self.max_conexoes = max_conexoes
        self.downloads_ativos = {}
        self.lock = threading.Lock()

    def iniciar_download(self, peer_id: str, arquivo: ArquivoInfo, diretorio: str) -> bool:
        """Inicia download paralelo de um arquivo"""
        caminho = os.path.join(diretorio, arquivo.nome)
        
        # Cria arquivo vazio do tamanho correto
        with open(caminho, 'wb') as f:
            f.truncate(arquivo.tamanho)
        
        # Inicializa estado do download
        with self.lock:
            self.downloads_ativos[arquivo.nome] = {
                'arquivo': arquivo,
                'partes_pendentes': set(range(len(arquivo.partes))),
                'partes_baixadas': set(),
                'caminho': caminho,
                'erros': 0
            }
        
        # Inicia threads de download
        for _ in range(min(self.max_conexoes, len(arquivo.partes))):
            thread = threading.Thread(
                target=self._thread_download,
                args=(peer_id, arquivo.nome)
            )
            thread.daemon = True
            thread.start()
        
        return True

    def _thread_download(self, peer_id: str, nome_arquivo: str):
        """Thread que baixa partes do arquivo"""
        max_tentativas = 3  # Número máximo de tentativas por parte
        
        while True:
            # Pega próxima parte para baixar
            with self.lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return
                
                if not download['partes_pendentes']:
                    # Verifica se download terminou
                    if len(download['partes_baixadas']) == len(download['arquivo'].partes):
                        self._finalizar_download(nome_arquivo)
                    return
                
                parte_num = next(iter(download['partes_pendentes']))
                download['partes_pendentes'].remove(parte_num)
            
            # Baixa a parte
            tentativas = 0
            while tentativas < max_tentativas:
                try:
                    parte = download['arquivo'].partes[parte_num]
                    if self._baixar_parte(peer_id, download['arquivo'], parte, download['caminho']):
                        with self.lock:
                            download['partes_baixadas'].add(parte_num)
                        break
                    tentativas += 1
                except Exception as e:
                    logging.error(f"Tentativa {tentativas + 1} falhou para parte {parte_num}: {e}")
                    tentativas += 1
            
            if tentativas == max_tentativas:
                with self.lock:
                    download['partes_pendentes'].add(parte_num)
                    download['erros'] += 1
                    if download['erros'] > len(download['arquivo'].partes) * 2:
                        logging.error(f"Muitos erros no download de {nome_arquivo}")
                        return
                    
    def _baixar_parte(self, peer_id: str, arquivo: ArquivoInfo, parte: PartInfo, caminho: str) -> bool:
        """Baixa uma parte específica do arquivo"""
        sock = None
        try:
            # Obtém informações do peer
            peer_info = self.peer_client._obter_info_peer(peer_id)
            if not peer_info:
                logging.error(f"Não foi possível obter informações do peer {peer_id}")
                return False

            # Estabelece conexão com o peer
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)  # Timeout de 30 segundos
            sock.connect((peer_info['ip'], peer_info['porta']))
            
            # Solicita parte específica
            mensagem = {
                "tipo": "parte_arquivo",
                "nome": arquivo.nome,
                "parte": parte.numero,
                "inicio": parte.inicio,
                "tamanho": parte.tamanho
            }
            sock.send(json.dumps(mensagem).encode())
            
            # Recebe dados
            recebido = 0
            buffer = bytearray()
            
            while recebido < parte.tamanho:
                dados = sock.recv(min(4096, parte.tamanho - recebido))
                if not dados:
                    raise Exception("Conexão fechada prematuramente")
                buffer.extend(dados)
                recebido += len(dados)
                logging.debug(f"Progresso parte {parte.numero}: {(recebido/parte.tamanho)*100:.1f}%")
            
            # Verifica hash
            hash_recebido = hashlib.sha256(buffer).hexdigest()
            if hash_recebido != parte.hash:
                raise Exception(f"Hash inválido para parte {parte.numero}")
            
            # Escreve no arquivo
            with open(caminho, 'r+b') as f:
                f.seek(parte.inicio)
                f.write(buffer)
            
            logging.info(f"Parte {parte.numero} baixada com sucesso")
            return True
            
        except Exception as e:
            logging.error(f"Erro baixando parte {parte.numero}: {str(e)}")
            return False
        finally:
            if sock:
                sock.close()

    def _finalizar_download(self, nome_arquivo: str):
        """Finaliza um download verificando sua integridade"""
        with self.lock:
            download = self.downloads_ativos.get(nome_arquivo)
            if not download:
                return
                
            # Verifica hash final
            with open(download['caminho'], 'rb') as f:
                hash_final = hashlib.sha256(f.read()).hexdigest()
                
            if hash_final != download['arquivo'].hash:
                logging.error(f"Hash final inválido para {nome_arquivo}")
                os.remove(download['caminho'])
            else:
                logging.info(f"Download de {nome_arquivo} concluído com sucesso")
                
            del self.downloads_ativos[nome_arquivo]

class PeerClient:
    def __init__(self, host: str = "localhost", porta: int = 0, diretorio: str = "compartilhado"):
        self.host = host
        self.porta = porta
        self.diretorio = diretorio
        self.peer_id = None
        self.arquivos: Dict[str, ArquivoInfo] = {}
        self.chats_ativos: Dict[str, socket.socket] = {}
        self.gerenciador_transferencia = GerenciadorTransferencia(self)
        
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

    def _obter_info_peer(self, peer_id: str) -> Optional[dict]:
        """Obtém informações de um peer do tracker"""
        resposta = self._enviar_comando_tracker({
            "comando": "info_peer",
            "peer_id": peer_id
        })
        
        if resposta["status"] == "sucesso":
            return {
                "ip": resposta["ip"],
                "porta": resposta["porta"]
            }
        return None

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
            
            resposta = self._enviar_comando_tracker(mensagem)
            
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
        
        # Cria informações do arquivo
        info = ArquivoInfo(nome, tamanho, "")
        
        # Calcula hashes
        with open(destino, 'rb') as f:
            # Hash do arquivo completo
            hash_obj = hashlib.sha256()
            for bloco in iter(lambda: f.read(4096), b''):
                hash_obj.update(bloco)
            info.hash = hash_obj.hexdigest()
            
            # Hash das partes
            info.calcular_hashes(destino)
        
        # Registra arquivo
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
            info_peer = self._obter_info_peer(peer_id)
            if not info_peer:
                return False
            
            # Conecta ao peer
            logging.info(f"Tentando conectar a {info_peer['ip']}:{info_peer['porta']}")
            socket_chat = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socket_chat.connect((info_peer['ip'], info_peer['porta']))
            
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
            info_peer = self._obter_info_peer(peer_id)
            if not info_peer:
                return False
            
            # Estabelece conexão inicial para obter metadados
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((info_peer['ip'], info_peer['porta']))
            
            # Solicita arquivo
            mensagem = {
                "tipo": "arquivo",
                "nome": nome_arquivo
            }
            sock.send(json.dumps(mensagem).encode())
            
            # Recebe metadados
            dados = sock.recv(4096).decode()
            metadados = json.loads(dados)
            
            if metadados.get("tipo") == "erro":
                logging.error(f"Erro ao solicitar arquivo: {metadados.get('mensagem')}")
                return False
            
            # Cria objeto ArquivoInfo com as informações recebidas
            info = ArquivoInfo(
                nome=metadados["nome"],
                tamanho=metadados["tamanho"],
                hash=metadados["hash"]
            )
            
            # Atualiza informações das partes
            info.partes = []
            for p in metadados["partes"]:
                info.partes.append(PartInfo(
                    numero=p["numero"],
                    inicio=p["inicio"],
                    tamanho=p["tamanho"],
                    hash=p["hash"]
                ))
            
            # Inicia download paralelo
            return self.gerenciador_transferencia.iniciar_download(peer_id, info, self.diretorio)
            
        except Exception as e:
            logging.error(f"Erro ao solicitar arquivo: {e}")
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
                # Envia informações do arquivo
                nome = mensagem["nome"]
                if nome in self.arquivos:
                    info = self.arquivos[nome]
                    # Envia metadados com informações das partes
                    metadados = {
                        "tipo": "metadados_arquivo",
                        "nome": nome,
                        "tamanho": info.tamanho,
                        "hash": info.hash,
                        "partes": [
                            {
                                "numero": p.numero,
                                "inicio": p.inicio,
                                "tamanho": p.tamanho,
                                "hash": p.hash
                            }
                            for p in info.partes
                        ]
                    }
                    sock.send(json.dumps(metadados).encode())
                else:
                    sock.send(json.dumps({
                        "tipo": "erro",
                        "mensagem": "Arquivo não encontrado"
                    }).encode())
                
            elif tipo == "parte_arquivo":
                # Envia parte específica do arquivo
                nome = mensagem["nome"]
                if nome in self.arquivos:
                    numero_parte = mensagem["parte"]
                    info = self.arquivos[nome]
                    
                    if 0 <= numero_parte < len(info.partes):
                        parte = info.partes[numero_parte]
                        caminho = os.path.join(self.diretorio, nome)
                        
                        try:
                            with open(caminho, 'rb') as f:
                                f.seek(parte.inicio)
                                dados = f.read(parte.tamanho)
                                # Verifica hash antes de enviar
                                hash_atual = hashlib.sha256(dados).hexdigest()
                                if hash_atual != parte.hash:
                                    raise Exception("Hash da parte não confere")
                                sock.send(dados)
                                
                            # Atualiza métricas
                            self._enviar_comando_tracker({
                                "comando": "metricas",
                                "tipo": "upload",
                                "peer_id": self.peer_id,
                                "bytes": parte.tamanho
                            })
                            logging.info(f"Parte {numero_parte} enviada com sucesso")
                        except Exception as e:
                            logging.error(f"Erro ao enviar parte {numero_parte}: {e}")
                    else:
                        logging.error(f"Parte {numero_parte} inválida")
                else:
                    logging.error(f"Arquivo {nome} não encontrado")
                
        except Exception as e:
            logging.error(f"Erro ao processar conexão: {e}")
        finally:
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
                self._enviar_comando_tracker(mensagem)
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