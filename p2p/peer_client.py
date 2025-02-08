import socket
import threading
import json
import time
import os
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from p2p.rate_limiter import GerenciadorBanda

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


class GerenciadorTransferencia:
    """Gerencia a transferência paralela de arquivos"""
    def __init__(self, peer_client, max_conexoes: int = 4):
        self.peer_client = peer_client
        self.max_conexoes = max_conexoes
        self.downloads_ativos = {}
        self.downloads_concluidos = set()
        self.global_lock = threading.Lock()  
        self.download_locks = {}  
        self.gerenciador_banda = GerenciadorBanda()


    def verificar_download_concluido(self, nome_arquivo: str) -> bool:
        """Verifica se um download foi concluído"""
        with self.global_lock:
            return nome_arquivo in self.downloads_concluidos

    def limpar_downloads_concluidos(self):
        """Limpa a lista de downloads concluídos"""
        with self.global_lock:
            self.downloads_concluidos.clear()
            
    def adicionar_arquivo(self, caminho: str):
        """Adiciona arquivo ao peer após download"""
        try:
            self.peer_client.adicionar_arquivo(caminho)
        except Exception as e:
            logging.error(f"Erro ao adicionar arquivo após download: {e}")
        
    def iniciar_download(self, peers_disponiveis: list, arquivo: ArquivoInfo, diretorio: str) -> bool:
        """Inicia download paralelo com melhor distribuição de peers"""
        with self.global_lock:
            if arquivo.nome in self.downloads_concluidos:
                logging.info(f"Arquivo {arquivo.nome} já foi baixado recentemente.")
                return False
            
            # Cria lock específico para este download
            self.download_locks[arquivo.nome] = threading.Lock()
        
        caminho = os.path.join(diretorio, arquivo.nome)
        
        # Cria arquivo vazio
        with open(caminho, 'wb') as f:
            f.write(b'\0' * arquivo.tamanho)
        
        # Distribui partes entre peers disponíveis de forma balanceada
        num_partes = len(arquivo.partes)
        num_peers = len(peers_disponiveis)
        partes_por_peer = num_partes // num_peers
        partes_extras = num_partes % num_peers
        
        distribuicao_partes = {}
        inicio = 0
        
        for i, peer in enumerate(peers_disponiveis):
            num_partes_peer = partes_por_peer + (1 if i < partes_extras else 0)
            fim = inicio + num_partes_peer
            distribuicao_partes[peer['peer_id']] = list(range(inicio, fim))
            inicio = fim
        
        # Inicializa estado do download
        with self.global_lock:
            self.downloads_ativos[arquivo.nome] = {
                'arquivo': arquivo,
                'partes_pendentes': set(range(len(arquivo.partes))),
                'partes_baixadas': set(),
                'partes_em_progresso': {},
                'peers_disponiveis': peers_disponiveis,
                'distribuicao_partes': distribuicao_partes,
                'caminho': caminho,
                'erros_por_peer': {peer['peer_id']: 0 for peer in peers_disponiveis},
                'total_erros': 0,
                'threads': [],
                'concluido': False,
                'stats_por_peer': {peer['peer_id']: {'bytes': 0, 'tempo': 0} for peer in peers_disponiveis}
            }

        # Inicia threads de download - uma por peer disponível
        threads = []
        for peer in peers_disponiveis:
            thread = threading.Thread(
                target=self._thread_download,
                args=(arquivo.nome, peer['peer_id']),
                name=f"Download-{arquivo.nome}-{peer['peer_id']}"
            )
            thread.daemon = True
            thread.start()
            threads.append(thread)
            logging.info(f"Iniciada thread de download para peer {peer['peer_id']}")

        # Armazena threads
        with self.global_lock:
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
        """Monitora o progresso do download com estatísticas por peer"""
        try:
            inicio_download = time.time()
            ultimo_progresso = time.time()
            bytes_ultimo_progresso = {}
            
            with self.global_lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return
                    
                tamanho_total = download['arquivo'].tamanho
                for peer_id in download['stats_por_peer']:
                    bytes_ultimo_progresso[peer_id] = 0
            
            while True:
                with self.global_lock:
                    download = self.downloads_ativos.get(nome_arquivo)
                    if not download:
                        return
                    
                    if not any(t.is_alive() for t in download['threads']):
                        break
                    
                    # Calcula estatísticas por peer
                    agora = time.time()
                    tempo_decorrido = agora - ultimo_progresso
                    
                    if tempo_decorrido >= 1.0:  # Atualiza a cada segundo
                        total_bytes = 0
                        logging.info("\nEstatísticas de download por peer:")
                        
                        for peer_id, stats in download['stats_por_peer'].items():
                            bytes_periodo = stats['bytes'] - bytes_ultimo_progresso[peer_id]
                            if bytes_periodo > 0:
                                velocidade = (bytes_periodo / 1024) / tempo_decorrido  # KB/s
                                logging.info(f"- Peer {peer_id}: {velocidade:.1f} KB/s")
                                bytes_ultimo_progresso[peer_id] = stats['bytes']
                                total_bytes += stats['bytes']
                        
                        progresso = (total_bytes / tamanho_total) * 100
                        logging.info(f"Progresso total: {min(100, progresso):.1f}%")
                        
                        ultimo_progresso = agora
                
                time.sleep(0.1)
            
            # Estatísticas finais
            tempo_total = time.time() - inicio_download
            self._finalizar_download(nome_arquivo, tempo_total)
            
        except Exception as e:
            logging.error(f"Erro no monitor de download: {e}")

    def _thread_download(self, nome_arquivo: str, peer_id: str):
        """Thread dedicada para download de partes de um peer específico"""
        max_tentativas = 3
        
        while True:
            parte_num = None
            
            # Obtém próxima parte designada para este peer
            with self.global_lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return

                if not download['partes_pendentes']:
                    return

                # Pega apenas partes designadas para este peer
                partes_designadas = set(download['distribuicao_partes'][peer_id])
                partes_disponiveis = partes_designadas & download['partes_pendentes']
                
                if not partes_disponiveis:
                    # Verifica se há partes pendentes de outros peers com muitos erros
                    todas_pendentes = download['partes_pendentes']
                    if todas_pendentes:
                        parte_num = min(todas_pendentes)  # Pega qualquer parte pendente
                    else:
                        return
                else:
                    parte_num = min(partes_disponiveis)

                download['partes_pendentes'].remove(parte_num)
                download['partes_em_progresso'][parte_num] = peer_id

            # Tenta baixar a parte
            inicio_download = time.time()
            tentativas = 0
            sucesso = False
            
            while tentativas < max_tentativas and not sucesso:
                try:
                    parte = download['arquivo'].partes[parte_num]
                    if self._baixar_parte(peer_id, download['arquivo'], parte, download['caminho']):
                        with self.global_lock:
                            download = self.downloads_ativos.get(nome_arquivo)
                            if download:
                                download['partes_baixadas'].add(parte_num)
                                download['partes_em_progresso'].pop(parte_num, None)
                                
                                # Atualiza estatísticas
                                tempo_download = time.time() - inicio_download
                                download['stats_por_peer'][peer_id]['bytes'] += parte.tamanho
                                download['stats_por_peer'][peer_id]['tempo'] += tempo_download
                        sucesso = True
                    else:
                        tentativas += 1
                        with self.global_lock:
                            download = self.downloads_ativos.get(nome_arquivo)
                            if download:
                                download['erros_por_peer'][peer_id] += 1
                                download['total_erros'] += 1
                except Exception as e:
                    logging.error(f"Erro ao baixar parte {parte_num} do peer {peer_id}: {e}")
                    tentativas += 1

            if not sucesso:
                with self.global_lock:
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
            with self.global_lock:
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
    
    def _finalizar_download(self, nome_arquivo: str, tempo_total: float):
        """Finaliza o processo de download"""
        try:
            with self.global_lock:
                download = self.downloads_ativos.get(nome_arquivo)
                if not download:
                    return

                # Verifica integridade do download
                arquivo = download['arquivo']
                caminho = download['caminho']

                # Calcula hash do arquivo baixado
                with open(caminho, 'rb') as f:
                    hash_obj = hashlib.sha256()
                    for bloco in iter(lambda: f.read(4096), b''):
                        hash_obj.update(bloco)
                    hash_calculado = hash_obj.hexdigest()

                # Verifica se o hash corresponde
                if hash_calculado != arquivo.hash:
                    logging.error(f"Erro: Hash do arquivo {nome_arquivo} não corresponde")
                    # Pode adicionar lógica para tentar baixar novamente
                    os.remove(caminho)
                    return

                # Registra download como concluído
                self.downloads_concluidos.add(nome_arquivo)
                
                # Adiciona o arquivo ao peer
                self.adicionar_arquivo(caminho)

                # Calcula estatísticas
                tamanho_total = arquivo.tamanho
                velocidade_media = (tamanho_total / 1024) / tempo_total  # KB/s

                # Logs de conclusão
                logging.info(f"\nDownload concluído: {nome_arquivo}")
                logging.info(f"Tamanho: {tamanho_total} bytes")
                logging.info(f"Tempo total: {tempo_total:.2f} segundos")
                logging.info(f"Velocidade média: {velocidade_media:.2f} KB/s")

                # Limpa estado do download
                del self.downloads_ativos[nome_arquivo]

        except Exception as e:
            logging.error(f"Erro ao finalizar download: {e}")

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