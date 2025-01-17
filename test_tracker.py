"""
Script de Teste para o Tracker P2P
=================================

Este script simula múltiplos peers se conectando ao tracker e realizando
diferentes operações para testar sua funcionalidade.

Funcionalidades testadas:
- Registro de peers
- Busca de arquivos
- Atualização de informações
- Comunicação com o tracker
"""

import socket
import json
import threading
import time
import random

class PeerTeste:
    """
    Classe que simula um peer para testes.
    """
    def __init__(self, host="localhost", porta=55555):
        """
        Inicializa um peer de teste.

        Args:
            host: Endereço do tracker
            porta: Porta do tracker
        """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((host, porta))
        self.peer_id = None
    
    def enviar_mensagem(self, mensagem):
        """
        Envia uma mensagem para o tracker e recebe a resposta.

        Args:
            mensagem: Dicionário com a mensagem a ser enviada

        Returns:
            dict: Resposta do tracker
        """
        self.socket.send(json.dumps(mensagem).encode('utf-8'))
        resposta = self.socket.recv(4096).decode('utf-8')
        return json.loads(resposta)
    
    def registrar(self, arquivos):
        """
        Registra o peer no tracker.

        Args:
            arquivos: Lista de arquivos compartilhados

        Returns:
            dict: Resposta do registro
        """
        mensagem = {
            "comando": "registrar",
            "porta": random.randint(8000, 9000),
            "arquivos": arquivos
        }
        resposta = self.enviar_mensagem(mensagem)
        if resposta["status"] == "sucesso":
            self.peer_id = resposta["peer_id"]
        return resposta
    
    def buscar_arquivos(self, termo):
        """
        Realiza busca por arquivos.

        Args:
            termo: Termo de busca

        Returns:
            dict: Resultados da busca
        """
        mensagem = {
            "comando": "buscar",
            "termo": termo
        }
        return self.enviar_mensagem(mensagem)
    
    def atualizar_info(self, arquivos, volume_upload):
        """
        Atualiza informações do peer.

        Args:
            arquivos: Nova lista de arquivos
            volume_upload: Novo volume de upload

        Returns:
            dict: Confirmação da atualização
        """
        mensagem = {
            "comando": "atualizar",
            "peer_id": self.peer_id,
            "arquivos": arquivos,
            "volume_upload": volume_upload
        }
        return self.enviar_mensagem(mensagem)
    
    def fechar(self):
        """Fecha a conexão com o tracker."""
        self.socket.close()

def testar_tracker():
    """
    Executa uma série de testes no tracker.
    """
    print("\nIniciando testes do tracker...")
    
    # Criar peers de teste
    peer1 = PeerTeste()
    peer2 = PeerTeste()
    peer3 = PeerTeste()
    
    try:
        # Teste 1: Registro de peers
        print("\n1. Testando registro de peers...")
        resp1 = peer1.registrar(["filme1.mp4", "documento1.pdf"])
        print(f"Peer 1 registrado: {resp1}")
        
        resp2 = peer2.registrar(["filme1.mp4", "musica1.mp3"])
        print(f"Peer 2 registrado: {resp2}")
        
        resp3 = peer3.registrar(["documento1.pdf", "imagem1.jpg"])
        print(f"Peer 3 registrado: {resp3}")
        
        time.sleep(2)
        
        # Teste 2: Busca de arquivos
        print("\n2. Testando busca de arquivos...")
        resultado_busca = peer1.buscar_arquivos("filme")
        print(f"Resultado da busca por 'filme': {json.dumps(resultado_busca, indent=2)}")
        
        # Teste 3: Atualização de informações
        print("\n3. Testando atualização de informações...")
        resultado_atualizacao = peer1.atualizar_info(
            ["filme1.mp4", "documento1.pdf", "novo_arquivo.txt"],
            volume_upload=50.5
        )
        print(f"Resultado da atualização: {resultado_atualizacao}")
        
        # Teste 4: Nova busca após atualização
        print("\n4. Testando nova busca após atualização...")
        resultado_busca = peer2.buscar_arquivos("novo_arquivo")
        print(f"Resultado da busca por 'novo_arquivo': {json.dumps(resultado_busca, indent=2)}")
        
    finally:
        # Fechar conexões
        peer1.fechar()
        peer2.fechar()
        peer3.fechar()

if __name__ == "__main__":
    testar_tracker()
