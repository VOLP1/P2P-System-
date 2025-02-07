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
            f.write(b'\0' * arquivo.tamanho)  # Preenche com zeros
        
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
        threads = []
        for _ in range(min(self.max_conexoes, len(arquivo.partes))):
            thread = threading.Thread(
                target=self._thread_download,
                args=(peer_id, arquivo.nome)
            )
            thread.daemon = True
            thread.start()
            threads.append(thread)
        
        # Aguarda todas as threads terminarem
        for thread in threads:
            thread.join()
        
        # Verifica status final
        with self.lock:
            download = self.downloads_ativos.get(arquivo.nome)
            if not download:
                return False
            
            if len(download['partes_baixadas']) == len(arquivo.partes):
                # Verifica hash final
                with open(caminho, 'rb') as f:
                    hash_final = hashlib.sha256(f.read()).hexdigest()
                if hash_final == arquivo.hash:
                    logging.info(f"Download de {arquivo.nome} concluído com sucesso")
                    del self.downloads_ativos[arquivo.nome]
                    return True
                else:
                    logging.error("Hash final inválido")
                    os.remove(caminho)
                    return False
            else:
                logging.error(f"Download incompleto: {len(download['partes_baixadas'])}/{len(arquivo.partes)} partes")
                os.remove(caminho)
                return False

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
                    return
                
                parte_num = next(iter(download['partes_pendentes']))
                download['partes_pendentes'].remove(parte_num)
            
            # Baixa a parte
            tentativas = 0
            sucesso = False
            while tentativas < max_tentativas and not sucesso:
                try:
                    parte = download['arquivo'].partes[parte_num]
                    if self._baixar_parte(peer_id, download['arquivo'], parte, download['caminho']):
                        with self.lock:
                            download['partes_baixadas'].add(parte_num)
                            logging.info(f"Parte {parte_num} baixada com sucesso ({len(download['partes_baixadas'])}/{len(download['arquivo'].partes)})")
                        sucesso = True
                    else:
                        tentativas += 1
                        logging.warning(f"Tentativa {tentativas} falhou para parte {parte_num}")
                except Exception as e:
                    logging.error(f"Erro na tentativa {tentativas + 1} para parte {parte_num}: {e}")
                    tentativas += 1
            
            if not sucesso:
                with self.lock:
                    download['partes_pendentes'].add(parte_num)
                    download['erros'] += 1
                    if download['erros'] > len(download['arquivo'].partes) * 2:
                        logging.error(f"Muitos erros no download de {nome_arquivo}")
                        return False

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
            sock.sendall(json.dumps(mensagem).encode())
            
            # Recebe dados
            recebido = 0
            buffer = bytearray()
            
            while recebido < parte.tamanho:
                dados = sock.recv(min(4096, parte.tamanho - recebido))
                if not dados:
                    logging.error(f"Conexão fechada prematuramente para parte {parte.numero}")
                    return False
                buffer.extend(dados)
                recebido += len(dados)
                if recebido % (128 * 1024) == 0:  # Log a cada 128KB
                    logging.debug(f"Progresso parte {parte.numero}: {(recebido/parte.tamanho)*100:.1f}%")
            
            # Verifica hash
            hash_recebido = hashlib.sha256(buffer).hexdigest()
            if hash_recebido != parte.hash:
                logging.error(f"Hash inválido para parte {parte.numero}")
                logging.error(f"Esperado: {parte.hash}")
                logging.error(f"Recebido: {hash_recebido}")
                return False
            
            # Escreve no arquivo usando lock para garantir escrita segura
            with self.lock:
                with open(caminho, 'r+b') as f:
                    f.seek(parte.inicio)
                    f.write(buffer)
            
            logging.info(f"Parte {parte.numero} baixada e verificada com sucesso")
            return True
            
        except Exception as e:
            logging.error(f"Erro baixando parte {parte.numero}: {str(e)}")
            return False
        finally:
            if sock:
                sock.close()

class PeerClient:
    def __init__(self, host: str = "localhost", porta: int = 0, diretorio: str = "compartilhado"):
        self.host = host
        self.porta = porta
        self.diretorio = diretorio
        self.peer_id = None
        self.arquivos: Dict[str, ArquivoInfo] = {}
        self.chats_ativos: Dict[str, socket.socket] = {}
        self.gerenciador_transferencia = GerenciadorTransferencia(self)
        self.socket_tracker = None
        self.lock_tracker = threading.Lock()
        
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

    def _reconectar_tracker(self) -> bool:
        """Reconecta ao tracker se necessário"""
        try:
            if self.socket_tracker is None:
                self.socket_tracker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket_tracker.connect((self.tracker_host, self.tracker_porta))
                return True
            return True
        except Exception as e:
            logging.error(f"Erro ao reconectar ao tracker: {e}")
            self.socket_tracker = None
            return False

    def _enviar_comando_tracker(self, comando: dict) -> dict:
        """Envia um comando para o tracker e recebe a resposta"""
        with self.lock_tracker:
            try:
                if not self._reconectar_tracker():
                    return {"status": "erro", "mensagem": "Não foi possível conectar ao tracker"}

                # Envia comando
                dados = json.dumps(comando).encode() + b'\n'
                self.socket_tracker.sendall(dados)
                
                # Recebe resposta
                resposta = self.socket_tracker.recv(4096).decode().strip()
                return json.loads(resposta)
                
            except Exception as e:
                logging.error(f"Erro ao enviar comando ao tracker: {e}")
                self.socket_tracker = None  # Força reconexão na próxima tentativa
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
            self.tracker_host = host
            self.tracker_porta = porta
            
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

    def solicitar_arquivo(self, peer_id: str, nome_arquivo: str) -> bool:
        """Solicita download de arquivo de outro peer"""
        try:
            # Obtém informações do peer
            info_peer = self._obter_info_peer(peer_id)
            if not info_peer:
                logging.error(f"Não foi possível obter informações do peer {peer_id}")
                return False
            
            # Estabelece conexão inicial para obter metadados
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect((info_peer['ip'], info_peer['porta']))
                
                # Solicita arquivo
                mensagem = {
                    "tipo": "arquivo",
                    "nome": nome_arquivo
                }
                sock.sendall(json.dumps(mensagem).encode())
                
                # Recebe metadados
                dados = sock.recv(4096).decode()
                metadados = json.loads(dados)
                
                if metadados.get("tipo") == "erro":
                    logging.error(f"Erro ao solicitar arquivo: {metadados.get('mensagem')}")
                    return False
                    
                if metadados.get("tipo") != "metadados_arquivo":
                    logging.error(f"Tipo de resposta inválido: {metadados.get('tipo')}")
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
                
                logging.info(f"Recebidos metadados do arquivo {nome_arquivo}")
                logging.info(f"Tamanho total: {info.tamanho} bytes")
                logging.info(f"Número de partes: {len(info.partes)}")
                
                # Inicia download paralelo
                return self.gerenciador_transferencia.iniciar_download(peer_id, info, self.diretorio)
                
            finally:
                sock.close()
                
        except Exception as e:
            logging.error(f"Erro ao solicitar arquivo: {str(e)}")
            logging.exception("Stack trace completo:")
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
                    sock.sendall(json.dumps(metadados).encode())
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
                            logging.info(f"Iniciando envio da parte {numero_parte}")
                            with open(caminho, 'rb') as f:
                                f.seek(parte.inicio)
                                dados = f.read(parte.tamanho)
                                
                                # Verifica tamanho lido
                                if len(dados) != parte.tamanho:
                                    logging.error(f"Tamanho lido ({len(dados)}) diferente do esperado ({parte.tamanho})")
                                    return
                                
                                # Verifica hash antes de enviar
                                hash_atual = hashlib.sha256(dados).hexdigest()
                                logging.info(f"Hash calculado para parte {numero_parte}: {hash_atual}")
                                logging.info(f"Hash esperado para parte {numero_parte}: {parte.hash}")
                                
                                if hash_atual != parte.hash:
                                    logging.error(f"Hash inválido para parte {numero_parte}")
                                    logging.error(f"Esperado: {parte.hash}")
                                    logging.error(f"Calculado: {hash_atual}")
                                    return
                                
                                # Envia os dados em blocos menores
                                bloco_size = 4096
                                bytes_enviados = 0
                                while bytes_enviados < len(dados):
                                    bloco = dados[bytes_enviados:bytes_enviados + bloco_size]
                                    sock.sendall(bloco)  # Usa sendall para garantir envio completo
                                    bytes_enviados += len(bloco)
                                    if bytes_enviados % (128 * 1024) == 0:  # Log a cada 128KB
                                        logging.info(f"Enviados {bytes_enviados}/{len(dados)} bytes da parte {numero_parte}")
                                
                                logging.info(f"Total de bytes enviados para parte {numero_parte}: {bytes_enviados}")
                            
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
                            logging.exception("Detalhes do erro:")
                    else:
                        logging.error(f"Parte {numero_parte} inválida")
                else:
                    logging.error(f"Arquivo {nome} não encontrado")
            
        except Exception as e:
            logging.error(f"Erro ao processar conexão: {e}")
            logging.exception("Detalhes do erro:")
        finally:
            if tipo != "chat":  # Não fecha a conexão de chat aqui
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
            self.chats_ativos[peer_id].sendall(json.dumps(dados).encode())
            return True
        except Exception as e:
            logging.error(f"Erro ao enviar mensagem: {e}")
            if peer_id in self.chats_ativos:
                del self.chats_ativos[peer_id]
            return False

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
            socket_chat.sendall(json.dumps(mensagem).encode())
            
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