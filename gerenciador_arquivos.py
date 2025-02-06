import os
import hashlib
from dataclasses import dataclass
import socket
import json
import logging
from typing import BinaryIO

@dataclass
class ArquivoInfo:
    """Informações sobre um arquivo para transferência"""
    nome: str
    tamanho: int
    hash: str
    partes: int = 1  # Inicialmente 1, será usado para transferência paralela depois

def calcular_hash_arquivo(arquivo: BinaryIO) -> str:
    """Calcula o hash SHA-256 de um arquivo"""
    sha256 = hashlib.sha256()
    for bloco in iter(lambda: arquivo.read(4096), b''):
        sha256.update(bloco)
    arquivo.seek(0)  # Volta para o início do arquivo
    return sha256.hexdigest()

class GerenciadorArquivos:
    """Gerencia o compartilhamento e download de arquivos"""
    
    def __init__(self, diretorio_compartilhado: str):
        self.diretorio = diretorio_compartilhado
        if not os.path.exists(diretorio_compartilhado):
            os.makedirs(diretorio_compartilhado)

    def adicionar_arquivo(self, caminho_arquivo: str) -> ArquivoInfo:
        """
        Adiciona um arquivo ao diretório compartilhado.
        
        Returns:
            ArquivoInfo com metadados do arquivo
        """
        nome_arquivo = os.path.basename(caminho_arquivo)
        destino = os.path.join(self.diretorio, nome_arquivo)
        
        # Se o arquivo não estiver no diretório compartilhado, copia
        if os.path.abspath(caminho_arquivo) != os.path.abspath(destino):
            with open(caminho_arquivo, 'rb') as origem, open(destino, 'wb') as dest:
                dest.write(origem.read())
        
        # Calcula informações do arquivo
        tamanho = os.path.getsize(destino)
        with open(destino, 'rb') as f:
            hash_arquivo = calcular_hash_arquivo(f)
        
        return ArquivoInfo(nome_arquivo, tamanho, hash_arquivo)

    def enviar_arquivo(self, socket_cliente: socket.socket, nome_arquivo: str):
        """Envia um arquivo para outro peer"""
        caminho = os.path.join(self.diretorio, nome_arquivo)
        
        try:
            # Envia metadados do arquivo
            with open(caminho, 'rb') as arquivo:
                info = ArquivoInfo(
                    nome=nome_arquivo,
                    tamanho=os.path.getsize(caminho),
                    hash=calcular_hash_arquivo(arquivo)
                )
                
                metadados = {
                    "nome": info.nome,
                    "tamanho": info.tamanho,
                    "hash": info.hash
                }
                socket_cliente.send(json.dumps(metadados).encode('utf-8'))
                
                # Aguarda confirmação
                if socket_cliente.recv(1024).decode('utf-8') != "ok":
                    raise Exception("Cliente não confirmou recebimento de metadados")
                
                # Envia o arquivo em blocos
                while True:
                    bloco = arquivo.read(4096)
                    if not bloco:
                        break
                    socket_cliente.send(bloco)
                
            logging.info(f"Arquivo {nome_arquivo} enviado com sucesso")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao enviar arquivo {nome_arquivo}: {str(e)}")
            return False

    def receber_arquivo(self, socket_origem: socket.socket) -> bool:
        """Recebe um arquivo de outro peer"""
        try:
            # Recebe metadados
            metadados = json.loads(socket_origem.recv(1024).decode('utf-8'))
            nome_arquivo = metadados["nome"]
            tamanho = metadados["tamanho"]
            hash_esperado = metadados["hash"]
            
            # Confirma recebimento dos metadados
            socket_origem.send("ok".encode('utf-8'))
            
            # Prepara para receber o arquivo
            caminho = os.path.join(self.diretorio, nome_arquivo)
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
                hash_recebido = calcular_hash_arquivo(arquivo)
                
            if hash_recebido != hash_esperado:
                os.remove(caminho)
                raise Exception("Hash do arquivo não corresponde")
            
            logging.info(f"Arquivo {nome_arquivo} recebido com sucesso")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao receber arquivo: {str(e)}")
            if 'caminho' in locals() and os.path.exists(caminho):
                os.remove(caminho)
            return False