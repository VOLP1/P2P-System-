# criar_teste.py
import os
import random

def criar_arquivo_teste(caminho: str, tamanho_mb: int = 10):
    """Cria um arquivo de teste com o tamanho especificado em MB"""
    print(f"Criando arquivo de teste de {tamanho_mb}MB em {caminho}")
    
    tamanho_bytes = tamanho_mb * 1024 * 1024
    bloco_size = 1024 * 1024  # 1MB
    
    with open(caminho, 'wb') as f:
        bytes_escritos = 0
        while bytes_escritos < tamanho_bytes:
            bloco = bytes([random.randint(0, 255) for _ in range(min(bloco_size, tamanho_bytes - bytes_escritos))])
            f.write(bloco)
            bytes_escritos += len(bloco)
            if bytes_escritos % (5 * 1024 * 1024) == 0:  # Log a cada 5MB
                print(f"Progresso: {bytes_escritos / tamanho_bytes * 100:.1f}%")

if __name__ == "__main__":
    nome_arquivo = "arquivo_teste.dat"
    criar_arquivo_teste(nome_arquivo, 100)  # Cria arquivo de 100MB
    print(f"\nArquivo criado: {nome_arquivo}")
    print(f"Tamanho: {os.path.getsize(nome_arquivo)/1024/1024:.1f} MB")