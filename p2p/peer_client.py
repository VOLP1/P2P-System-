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
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class PartInfo:
    """Informações sobre uma parte do arquivo"""
    numero: int        # Número da parte
    inicio: int        # Posição inicial no arquivo
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

class GerenciadorBanda:
    """Gerenciador de banda simplificado com controle efetivo por peer"""
    def __init__(self, taxa_base: int = 1024 * 1024):  # 1 MB/s base
        self.taxa_base = taxa_base
        self.lock = threading.Lock()
        self.ultimas_transferencias = {}  # Último momento de transferência
        self.bytes_periodo = {}          # Bytes transferidos no período atual
        self.taxas_peers = {}            # Taxa atual de cada peer
    
    def atualizar_limite(self, peer_id: str, pontuacao: float):
        """Define taxa baseada na pontuação"""
        with self.lock:
            # Taxa entre 100KB/s e 2MB/s
            taxa_min = 100 * 1024  # 100 KB/s
            taxa_max = 2 * 1024 * 1024  # 2 MB/s
            
            self.taxas_peers[peer_id] = taxa_min + (taxa_max - taxa_min) * pontuacao
            self.ultimas_transferencias[peer_id] = time.time()
            self.bytes_periodo[peer_id] = 0
            
            return self.taxas_peers[peer_id] / 1024  # Retorna KB/s
    
    def controlar_taxa(self, peer_id: str, tamanho: int):
        """Controle estrito da taxa"""
        with self.lock:
            agora = time.time()
            
            if peer_id not in self.taxas_peers:
                self.atualizar_limite(peer_id, 0.1)  # Taxa mínima default
            
            taxa = self.taxas_peers[peer_id]
            ultimo = self.ultimas_transferencias[peer_id]
            bytes_atual = self.bytes_periodo[peer_id]
            
            # Reseta contador após 1 segundo
            if agora - ultimo >= 1.0:
                self.bytes_periodo[peer_id] = 0
                self.ultimas_transferencias[peer_id] = agora
                bytes_atual = 0
            
            # Verifica se excederia a taxa
            if bytes_atual + tamanho > taxa:
                tempo_espera = (bytes_atual + tamanho - taxa) / taxa + 0.1
                time.sleep(tempo_espera)
                self.bytes_periodo[peer_id] = tamanho
                self.ultimas_transferencias[peer_id] = time.time()
            else:
                self.bytes_periodo[peer_id] = bytes_atual + tamanho
    
    def obter_taxa_atual(self, peer_id: str) -> float:
        """Retorna taxa em KB/s"""
        with self.lock:
            return self.taxas_peers.get(peer_id, 100 * 1024) / 1024

class GerenciadorTransferencia:
    """Gerencia a transferência paralela de arquivos"""
    def __init__(self, peer_client, max_conexoes: int = 4):
        self.peer_client = peer_client
        self.max_conexoes = max_conexoes
        self.downloads_ativos = {}
        self.downloads_concluidos = set()
        self.lock = threading.Lock()
        self.gerenciador_banda = GerenciadorBanda()


    def verificar_download_concluido(self, nome_arquivo: str) -> bool:
        """Verifica se um download foi concluído"""
        with self.lock:
            return nome_arquivo in self.downloads_concluidos

    def limpar_downloads_concluidos(self):
        """Limpa a lista de downloads concluídos"""
        with self.lock:
            self.downloads_concluidos.clear()
            
    def adicionar_arquivo(self, caminho: str):
        """Adiciona arquivo ao peer após download"""
        try:
            self.peer_client.adicionar_arquivo(caminho)
        except Exception as e:
            logging.error(f"Erro ao adicionar arquivo após download: {e}")
        
    def iniciar_download(self, peers_disponiveis: list, arquivo: ArquivoInfo, diretorio: str) -> bool:
        """Inicia download paralelo de um arquivo de múltiplos peers"""
        with self.lock:
            if arquivo.nome in self.downloads_concluidos:
                logging.info(f"Arquivo {arquivo.nome} já foi baixado recentemente.")
                return False
        
        caminho = os.path.join(diretorio, arquivo.nome)
        
        # Cria arquivo vazio
        with open(caminho, 'wb') as f:
            f.write(b'\0' * arquivo.tamanho)
        
        # Estado do download
        with self.lock:
            self.downloads_ativos[arquivo.nome] = {
                'arquivo': arquivo,
                'partes_pendentes': set(range(len(arquivo.partes))),
                'partes_baixadas': set(),
                'partes_em_progresso': {},
                'peers_disponiveis': peers_disponiveis,
                'caminho': caminho,
                'erros_por_peer': {peer['peer_id']: 0 for peer in peers_disponiveis},
                'total_erros': 0,
                'threads': [],
                'concluido': False
            }

        # Inicia threads de download
        num_conexoes = min(self.max_conexoes, len(peers_disponiveis), len(arquivo.partes))
        threads = []
        for i in range(num_conexoes):
            thread = threading.Thread(
                target=self._thread_download,
                args=(arquivo.nome, i),
                name=f"Download-{arquivo.nome}-{i}"
            )
            thread.daemon = True
            thread.start()
            threads.append(thread)
            logging.info(f"Iniciada thread de download {i} para {arquivo.nome}")

        # Armazena threads
        with self.lock:
            self.downloads_ativos[arquivo.nome]['threads'] = threads

        # Thread de monitoramento
        monitor_thread = threading.Thread(
            target=self._monitorar_download,
            args=(arquivo.nome,),
            name=f"Monitor-{arquivo.nome}"
        )
        monitor_thread.daemon = True
        monitor_thread.start()

        return True

    def _monitorar_download(self, nome_arquivo: str):
        """Monitora o progresso do download com medições mais precisas"""
        try:
            inicio_download = time.time()
            ultimo_progresso = time.time()
            bytes_ultimo_progresso = 0
            
            with self.lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return
                tamanho_total = download['arquivo'].tamanho
            
            # Loop de monitoramento
            threads = download['threads']
            threads_ativas = {t.name: True for t in threads}
            
            while any(threads_ativas.values()):
                # Atualiza status das threads
                for thread in threads:
                    if not thread.is_alive() and threads_ativas[thread.name]:
                        threads_ativas[thread.name] = False
                
                # Calcula progresso atual
                with self.lock:
                    download = self.downloads_ativos.get(nome_arquivo)
                    if not download:
                        return
                    
                    bytes_baixados = len(download['partes_baixadas']) * download['arquivo'].tamanho_parte
                    
                    # Calcula velocidade instantânea
                    agora = time.time()
                    tempo_decorrido = agora - ultimo_progresso
                    
                    bytes_periodo = bytes_baixados - bytes_ultimo_progresso
                    if tempo_decorrido > 0:
                        velocidade = (bytes_periodo / 1024) / tempo_decorrido  # KB/s
                        progresso = (bytes_baixados / tamanho_total) * 100
                        
                        logging.info(f"Progresso: {min(100, progresso):.1f}% - Velocidade: {velocidade:.1f} KB/s")
                        
                        ultimo_progresso = agora
                        bytes_ultimo_progresso = bytes_baixados
                
                time.sleep(0.1)  # Reduz uso de CPU
            
            # Estatísticas finais
            tempo_total = time.time() - inicio_download
            tamanho_mb = tamanho_total / (1024 * 1024)
            velocidade_media = (tamanho_total / 1024) / tempo_total  # KB/s

            # Verifica hash final
            with self.lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return
                caminho = download['caminho']
                
            try:
                with open(caminho, 'rb') as f:
                    hash_final = hashlib.sha256(f.read()).hexdigest()
                
                if hash_final == download['arquivo'].hash:
                    logging.info(f"\nDownload de {nome_arquivo} concluído com sucesso!")
                    logging.info(f"Estatísticas finais:")
                    logging.info(f"- Tamanho total: {tamanho_mb:.2f} MB")
                    logging.info(f"- Tempo total: {tempo_total:.2f} segundos")
                    logging.info(f"- Velocidade média: {velocidade_media:.2f} KB/s")
                    
                    self.adicionar_arquivo(caminho)
                    download['concluido'] = True
                    self.downloads_concluidos.add(nome_arquivo)
                else:
                    logging.error(f"Hash final inválido para {nome_arquivo}")
                    os.remove(caminho)
            except Exception as e:
                logging.error(f"Erro ao verificar hash final: {e}")
            
            with self.lock:
                self.downloads_ativos.pop(nome_arquivo, None)

        except Exception as e:
            logging.error(f"Erro no monitor de download: {e}")

    def _thread_download(self, nome_arquivo: str, thread_id: int):
        """Thread que baixa partes do arquivo"""
        max_tentativas = 3
        
        while True:
            parte_num = None
            peer = None
            
            with self.lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return

                if not download['partes_pendentes'] and not download['partes_em_progresso']:
                    return

                if not download['partes_pendentes']:
                    return

                parte_num = next(iter(download['partes_pendentes']))
                
                peers_disponiveis = download['peers_disponiveis']
                peers_em_uso = set(download['partes_em_progresso'].values())
                
                peers_possiveis = [p for p in peers_disponiveis if p['peer_id'] not in peers_em_uso]
                if not peers_possiveis:
                    peers_possiveis = peers_disponiveis
                
                peer = self._selecionar_melhor_peer(peers_possiveis, download['erros_por_peer'])
                
                if not peer:
                    logging.error(f"Thread {thread_id}: Nenhum peer disponível")
                    return

                download['partes_pendentes'].remove(parte_num)
                download['partes_em_progresso'][parte_num] = peer['peer_id']

            # Tenta baixar a parte
            tentativas = 0
            sucesso = False
            
            while tentativas < max_tentativas and not sucesso:
                try:
                    parte = download['arquivo'].partes[parte_num]
                    if self._baixar_parte(peer['peer_id'], download['arquivo'], parte, download['caminho']):
                        with self.lock:
                            download = self.downloads_ativos.get(nome_arquivo)
                            if download:
                                download['partes_baixadas'].add(parte_num)
                                download['partes_em_progresso'].pop(parte_num, None)
                        sucesso = True
                    else:
                        tentativas += 1
                        with self.lock:
                            download = self.downloads_ativos.get(nome_arquivo)
                            if download:
                                download['erros_por_peer'][peer['peer_id']] += 1
                                download['total_erros'] += 1
                except Exception as e:
                    logging.error(f"Thread {thread_id}: Erro ao baixar parte {parte_num}: {e}")
                    tentativas += 1

            if not sucesso:
                with self.lock:
                    download = self.downloads_ativos.get(nome_arquivo)
                    if download:
                        download['partes_pendentes'].add(parte_num)
                        download['partes_em_progresso'].pop(parte_num, None)
                        if download['total_erros'] > len(download['arquivo'].partes) * 3:
                            return

    def _baixar_parte(self, peer_id: str, arquivo: ArquivoInfo, parte: PartInfo, caminho: str) -> bool:
        """Baixa uma parte específica do arquivo"""
        sock = None
        try:
            peer_info = self.peer_client._obter_info_peer(peer_id)
            if not peer_info:
                return False
            
            # Define taxa baseada na pontuação
            taxa_kb = self.gerenciador_banda.atualizar_limite(peer_id, peer_info.get('pontuacao', 0.1))
            logging.debug(f"Taxa para peer {peer_id}: {taxa_kb:.1f} KB/s")

            # Conecta ao peer
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect((peer_info['ip'], peer_info['porta']))
            
            # Solicita parte
            mensagem = {
                "tipo": "parte_arquivo",
                "nome": arquivo.nome,
                "parte": parte.numero,
                "inicio": parte.inicio,
                "tamanho": parte.tamanho
            }
            sock.sendall(json.dumps(mensagem).encode())
            
            # Recebe dados com controle de taxa
            recebido = 0
            buffer = bytearray()
            
            # Define tamanho do bloco - 32KB para controle mais preciso
            bloco_size = 32 * 1024
            
            while recebido < parte.tamanho:
                # Controle de taxa antes de cada bloco
                self.gerenciador_banda.controlar_taxa(peer_id, bloco_size)
                
                # Recebe bloco
                dados = sock.recv(min(bloco_size, parte.tamanho - recebido))
                if not dados:
                    return False
                    
                buffer.extend(dados)
                recebido += len(dados)
            
            # Verifica hash
            hash_recebido = hashlib.sha256(buffer).hexdigest()
            if hash_recebido != parte.hash:
                logging.error(f"Hash inválido para parte {parte.numero}")
                return False
            
            # Escreve no arquivo
            with self.lock:
                with open(caminho, 'r+b') as f:
                    f.seek(parte.inicio)
                    f.write(buffer)
            
            return True
            
        except Exception as e:
            logging.error(f"Erro baixando parte {parte.numero}: {str(e)}")
            return False
        finally:
            if sock:
                sock.close()

    def _selecionar_melhor_peer(self, peers_disponiveis: list, erros_por_peer: dict) -> dict:
        """Seleciona o melhor peer baseado na pontuação e histórico de erros"""
        melhor_peer = None
        maior_pontuacao = -1
        
        for peer in peers_disponiveis:
            pontuacao_ajustada = peer['pontuacao'] / (1 + erros_por_peer.get(peer['peer_id'], 0))
            if pontuacao_ajustada > maior_pontuacao:
                maior_pontuacao = pontuacao_ajustada
                melhor_peer = peer
        
        return melhor_peer

class PeerClient:
    def __init__(self, host: str = "localhost", porta: int = 0, diretorio: str = "compartilhado"):
        self.host = host
        self.porta = porta
        self.diretorio = diretorio
        self.peer_id = None
        self.arquivos = {}
        self.chats_ativos = {}
        self.gerenciador_transferencia = GerenciadorTransferencia(self)
        self.socket_tracker = None
        self.lock_tracker = threading.Lock()
        
        os.makedirs(diretorio, exist_ok=True)
        
        self.socket_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_servidor.bind((host, porta))
        self.porta = self.socket_servidor.getsockname()[1]
        
        self.socket_servidor.listen(5)
        self.thread_servidor = threading.Thread(target=self._aceitar_conexoes)
        self.thread_servidor.daemon = True
        self.thread_servidor.start()
        
        logging.info(f"Peer iniciado em {host}:{self.porta}")


    

    def _reconectar_tracker(self) -> bool:
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
        with self.lock_tracker:
            try:
                if not self._reconectar_tracker():
                    return {"status": "erro", "mensagem": "Não foi possível conectar ao tracker"}

                dados = json.dumps(comando).encode() + b'\n'
                self.socket_tracker.sendall(dados)
                
                resposta = self.socket_tracker.recv(4096).decode().strip()
                return json.loads(resposta)
                
            except Exception as e:
                logging.error(f"Erro ao enviar comando ao tracker: {e}")
                self.socket_tracker = None
                return {"status": "erro", "mensagem": str(e)}

    def _obter_info_peer(self, peer_id: str) -> Optional[dict]:
        resposta = self._enviar_comando_tracker({
            "comando": "info_peer",
            "peer_id": peer_id
        })
        
        if resposta["status"] == "sucesso":
            return {
                "ip": resposta["ip"],
                "porta": resposta["porta"],
                "pontuacao": resposta.get("pontuacao", 0.1)
            }
        return None

    def conectar_tracker(self, host: str, porta: int) -> bool:
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
                
                self.thread_heartbeat = threading.Thread(target=self._enviar_heartbeat)
                self.thread_heartbeat.daemon = True
                self.thread_heartbeat.start()
                
                return True
            return False
            
        except Exception as e:
            logging.error(f"Erro ao conectar ao tracker: {e}")
            return False

    def adicionar_arquivo(self, caminho: str) -> ArquivoInfo:
        nome = os.path.basename(caminho)
        destino = os.path.join(self.diretorio, nome)
        
        if os.path.abspath(caminho) != os.path.abspath(destino):
            with open(caminho, 'rb') as origem, open(destino, 'wb') as dest:
                dest.write(origem.read())
        
        tamanho = os.path.getsize(destino)
        info = ArquivoInfo(nome, tamanho, "")
        
        with open(destino, 'rb') as f:
            hash_obj = hashlib.sha256()
            for bloco in iter(lambda: f.read(4096), b''):
                hash_obj.update(bloco)
            info.hash = hash_obj.hexdigest()
            
            info.calcular_hashes(destino)
        
        self.arquivos[nome] = info
        
        if hasattr(self, 'socket_tracker') and self.peer_id:
            self._enviar_comando_tracker({
                "comando": "atualizar",
                "peer_id": self.peer_id,
                "arquivos": list(self.arquivos.keys())
            })
        
        return info

    def solicitar_arquivo(self, nome_arquivo: str) -> bool:
        """Solicita download de arquivo"""
        if self.gerenciador_transferencia.verificar_download_concluido(nome_arquivo):
            logging.info(f"Arquivo {nome_arquivo} já foi baixado recentemente.")
            return True

        try:
            resposta = self._enviar_comando_tracker({
                "comando": "buscar",
                "termo": nome_arquivo
            })
            
            if resposta["status"] != "sucesso":
                logging.error(f"Erro ao buscar arquivo: {resposta.get('mensagem')}")
                return False
                
            resultados = resposta["resultados"]
            if nome_arquivo not in resultados:
                logging.error(f"Arquivo {nome_arquivo} não encontrado na rede")
                return False
                
            peers_disponiveis = resultados[nome_arquivo]
            if not peers_disponiveis:
                logging.error("Nenhum peer disponível com o arquivo")
                return False
                
            primeiro_peer = peers_disponiveis[0]
            info_peer = self._obter_info_peer(primeiro_peer['peer_id'])
            if not info_peer:
                return False
                
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect((info_peer['ip'], info_peer['porta']))
                
                mensagem = {
                    "tipo": "arquivo",
                    "nome": nome_arquivo
                }
                sock.sendall(json.dumps(mensagem).encode())
                
                tamanho_str = ""
                while True:
                    char = sock.recv(1).decode()
                    if char == '\n':
                        break
                    tamanho_str += char
                    
                tamanho = int(tamanho_str)
                
                dados = bytearray()
                while len(dados) < tamanho:
                    chunk = sock.recv(min(4096, tamanho - len(dados)))
                    if not chunk:
                        raise Exception("Conexão fechada prematuramente")
                    dados.extend(chunk)
                
                metadados = json.loads(dados.decode())
                if metadados.get("tipo") == "erro":
                    logging.error(f"Erro ao solicitar arquivo: {metadados.get('mensagem')}")
                    return False
                    
                if metadados.get("tipo") != "metadados_arquivo":
                    logging.error(f"Tipo de resposta inválido: {metadados.get('tipo')}")
                    return False
                
                info = ArquivoInfo(
                    nome=metadados["nome"],
                    tamanho=metadados["tamanho"],
                    hash=metadados["hash"]
                )
                
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
                
                return self.gerenciador_transferencia.iniciar_download(peers_disponiveis, info, self.diretorio)
                
            finally:
                sock.close()
                
        except Exception as e:
            logging.error(f"Erro ao solicitar arquivo: {str(e)}")
            return False

    def _aceitar_conexoes(self):
        while True:
            try:
                sock, addr = self.socket_servidor.accept()
                thread = threading.Thread(target=self._processar_conexao, args=(sock,))
                thread.daemon = True
                thread.start()
            except Exception as e:
                logging.error(f"Erro ao aceitar conexão: {e}")

    def _processar_conexao(self, sock: socket.socket):
        try:
            dados = sock.recv(1024)
            if not dados:
                return
                
            mensagem = json.loads(dados.decode())
            tipo = mensagem.get("tipo")
            
            if tipo == "arquivo":
                nome = mensagem["nome"]
                if nome in self.arquivos:
                    info = self.arquivos[nome]
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
                    dados_json = json.dumps(metadados).encode()
                    tamanho = len(dados_json)
                    sock.sendall(f"{tamanho}\n".encode())
                    sock.sendall(dados_json)
                else:
                    sock.send(json.dumps({
                        "tipo": "erro",
                        "mensagem": "Arquivo não encontrado"
                    }).encode())
                
            elif tipo == "parte_arquivo":
                nome = mensagem["nome"]
                if nome in self.arquivos:
                    numero_parte = mensagem["parte"]
                    info = self.arquivos[nome]
                    
                    if 0 <= numero_parte < len(info.partes):
                        parte = info.partes[numero_parte]
                        caminho = os.path.join(self.diretorio, nome)
                        
                        with open(caminho, 'rb') as f:
                            f.seek(parte.inicio)
                            dados = f.read(parte.tamanho)
                            sock.sendall(dados)
                            
                        self._enviar_comando_tracker({
                            "comando": "metricas",
                            "tipo": "upload",
                            "peer_id": self.peer_id,
                            "bytes": parte.tamanho
                        })
            if tipo == "chat":
                # Aceita conexão de chat
                peer_id = mensagem["peer_id"]
                logging.info(f"Recebida conexão de chat de {peer_id}")
                
                # Envia confirmação
                resposta = {
                    "tipo": "chat_aceito",
                    "peer_id": self.peer_id
                }
                sock.sendall(json.dumps(resposta).encode())
                
                self.chats_ativos[peer_id] = sock
                thread = threading.Thread(
                    target=self._receber_mensagens_chat,
                    args=(peer_id, sock),
                    name=f"Chat-{peer_id}"
                )
                thread.daemon = True
                thread.start()
                # Retorna sem fechar o socket
                return
                        
        except Exception as e:
            logging.error(f"Erro ao processar conexão: {e}")

        if tipo != "chat":
            sock.close()


    def iniciar_chat(self, peer_id: str) -> bool:
            """Inicia uma sessão de chat com outro peer"""
            if peer_id in self.chats_ativos:
                return True
                
            try:
                info_peer = self._obter_info_peer(peer_id)
                if not info_peer:
                    logging.error(f"Não foi possível obter informações do peer {peer_id}")
                    return False
                
                # Conecta ao peer
                logging.info(f"Conectando ao peer {peer_id} em {info_peer['ip']}:{info_peer['porta']}")
                socket_chat = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                socket_chat.settimeout(10)  # Timeout de 10 segundos
                socket_chat.connect((info_peer['ip'], info_peer['porta']))
                
                # Envia identificação
                mensagem = {
                    "tipo": "chat",
                    "peer_id": self.peer_id
                }
                socket_chat.sendall(json.dumps(mensagem).encode())
                
                # Aguarda confirmação
                try:
                    dados = socket_chat.recv(1024)
                    if not dados:
                        raise Exception("Conexão fechada sem confirmação")
                        
                    resposta = json.loads(dados.decode())
                    if resposta.get("tipo") != "chat_aceito":
                        raise Exception("Resposta inválida do peer")
                        
                    logging.info(f"Chat estabelecido com {peer_id}")
                except Exception as e:
                    logging.error(f"Erro na confirmação do chat: {e}")
                    socket_chat.close()
                    return False
                
                # Remove timeout após estabelecer conexão
                socket_chat.settimeout(None)
                
                # Registra conexão
                self.chats_ativos[peer_id] = socket_chat
                
                # Inicia thread de recebimento
                thread = threading.Thread(
                    target=self._receber_mensagens_chat,
                    args=(peer_id, socket_chat),
                    name=f"Chat-{peer_id}"
                )
                thread.daemon = True
                thread.start()
                
                logging.info(f"Chat iniciado com {peer_id}")
                print(f"\nChat iniciado com {peer_id}. Digite suas mensagens:")
                return True
                
            except Exception as e:
                logging.error(f"Erro ao iniciar chat: {str(e)}")
                return False

    def _receber_mensagens_chat(self, peer_id: str, sock: socket.socket):
        """Processa mensagens de chat"""
        sock.settimeout(None)  # Remove timeout para recebimento contínuo
        
        try:
            while True:
                try:
                    dados = sock.recv(1024)
                    if not dados:
                        logging.info(f"Conexão de chat com {peer_id} encerrada")
                        break
                        
                    mensagem = json.loads(dados.decode())
                    if mensagem["tipo"] == "mensagem":
                        print(f"\nMensagem de {peer_id}: {mensagem['conteudo']}")
                        print("(p2p) ", end='', flush=True)
                        
                except json.JSONDecodeError:
                    logging.error(f"Mensagem inválida recebida de {peer_id}")
                    continue
                    
        except Exception as e:
            logging.error(f"Erro ao receber mensagens de {peer_id}: {e}")
        finally:
            with self.lock_tracker:  # Adicione este lock como atributo da classe
                if peer_id in self.chats_ativos:
                    del self.chats_ativos[peer_id]
                try:
                    sock.close()
                except:
                    pass
            logging.info(f"Chat com {peer_id} encerrado")
            print(f"\nChat com {peer_id} encerrado")
            print("(p2p) ", end='', flush=True)    

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

    def chat(self, peer_id: str):
        """Inicia uma sessão de chat interativa com outro peer"""
        if not self.iniciar_chat(peer_id):
            logging.error(f"Não foi possível iniciar chat com {peer_id}")
            return

        print(f"\nChat iniciado com {peer_id}")
        
        while True:
            try:
                mensagem = input("Mensagem para %s (ou 'sair' para encerrar): " % peer_id)
                if mensagem.lower() == 'sair':
                    break
                    
                if not self.enviar_mensagem_chat(peer_id, mensagem):
                    logging.error("Erro ao enviar mensagem")
                    break
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(f"Erro no chat: {e}")
                break
        
        # Limpa conexão ao sair
        if peer_id in self.chats_ativos:
            try:
                self.chats_ativos[peer_id].close()
            except:
                pass
            del self.chats_ativos[peer_id]
        
        print(f"\nChat com {peer_id} encerrado")


    def _enviar_heartbeat(self):
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