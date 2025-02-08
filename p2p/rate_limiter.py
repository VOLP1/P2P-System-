import time
import threading
from typing import Dict

class GerenciadorBanda:
    """Controle de taxa global por download"""
    def __init__(self, taxa_base: int = 2 * 1024 * 1024):  # 2 MB/s base
        self.taxa_base = taxa_base
        self.lock = threading.Lock()
        self.taxas = {}             # Taxa máxima por peer
        self.janela_bytes = {}      # Bytes na janela atual por peer
        self.janela_inicio = {}     # Início da janela atual por peer
        self.JANELA_TEMPO = 0.1     # 100ms para controle mais granular
    
    def atualizar_limite(self, peer_id: str, pontuacao: float):
        """Define taxa baseada na pontuação"""
        with self.lock:
            # Taxa entre 200KB/s e 2MB/s
            taxa_min = 200 * 1024    # 200 KB/s
            taxa_max = self.taxa_base  # 2 MB/s
            
            self.taxas[peer_id] = taxa_min + (taxa_max - taxa_min) * pontuacao
            self.janela_bytes[peer_id] = 0
            self.janela_inicio[peer_id] = time.time()
            
            return self.taxas[peer_id] / 1024  # Retorna KB/s
    
    def controlar_taxa(self, peer_id: str, tamanho: int):
        """Controle de taxa por janela deslizante"""
        with self.lock:
            if peer_id not in self.taxas:
                self.atualizar_limite(peer_id, 0.1)
            
            agora = time.time()
            
            # Se passou o tempo da janela, reseta
            if agora - self.janela_inicio[peer_id] >= self.JANELA_TEMPO:
                self.janela_bytes[peer_id] = 0
                self.janela_inicio[peer_id] = agora
            
            # Calcula bytes permitidos na janela atual
            taxa = self.taxas[peer_id]
            bytes_permitidos = taxa * self.JANELA_TEMPO
            bytes_atuais = self.janela_bytes[peer_id]
            
            # Se vai exceder, espera
            if bytes_atuais + tamanho > bytes_permitidos:
                tempo_espera = (bytes_atuais + tamanho - bytes_permitidos) / taxa
                time.sleep(tempo_espera)
                # Após espera, inicia nova janela
                self.janela_bytes[peer_id] = tamanho
                self.janela_inicio[peer_id] = time.time()
            else:
                self.janela_bytes[peer_id] += tamanho

    def obter_taxa_atual(self, peer_id: str) -> float:
        """Retorna taxa atual em KB/s"""
        with self.lock:
            if peer_id not in self.janela_inicio:
                return 0.0
            
            agora = time.time()
            tempo_janela = agora - self.janela_inicio[peer_id]
            if tempo_janela <= 0:
                return 0.0
                
            return (self.janela_bytes[peer_id] / 1024) / tempo_janela