# teste_paralelo.py
import os
import time
import logging
import random
import shutil
import hashlib
from tracker import Tracker
from peer_client import PeerClient
import threading

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def criar_arquivo_grande(caminho: str, tamanho_mb: int = 10):
    """Cria um arquivo de teste com o tamanho especificado em MB"""
    logging.info(f"Criando arquivo de teste de {tamanho_mb}MB em {caminho}")
    
    tamanho_bytes = tamanho_mb * 1024 * 1024
    bloco_size = 1024 * 1024  # 1MB
    
    with open(caminho, 'wb') as f:
        bytes_escritos = 0
        while bytes_escritos < tamanho_bytes:
            # Gera dados aleatórios mas verificáveis
            bloco = bytes([random.randint(0, 255) for _ in range(min(bloco_size, tamanho_bytes - bytes_escritos))])
            f.write(bloco)
            bytes_escritos += len(bloco)
            if bytes_escritos % (5 * 1024 * 1024) == 0:  # Log a cada 5MB
                logging.info(f"Progresso: {bytes_escritos / tamanho_bytes * 100:.1f}%")

def verificar_arquivos(arquivo1: str, arquivo2: str) -> bool:
    """Verifica se dois arquivos são idênticos"""
    logging.info(f"Iniciando verificação de arquivos")
    logging.info(f"Arquivo 1: {arquivo1}")
    logging.info(f"Arquivo 2: {arquivo2}")
    
    if not os.path.exists(arquivo2):
        logging.error(f"Arquivo {arquivo2} não existe")
        return False
        
    tamanho1 = os.path.getsize(arquivo1)
    tamanho2 = os.path.getsize(arquivo2)
    logging.info(f"Tamanho arquivo 1: {tamanho1}")
    logging.info(f"Tamanho arquivo 2: {tamanho2}")
    
    if tamanho1 != tamanho2:
        logging.error(f"Tamanhos diferentes: {tamanho1} != {tamanho2}")
        return False
    
    # Lê e compara em blocos para evitar carregar arquivo inteiro na memória
    tamanho_bloco = 8192
    with open(arquivo1, 'rb') as f1, open(arquivo2, 'rb') as f2:
        posicao = 0
        while True:
            bloco1 = f1.read(tamanho_bloco)
            bloco2 = f2.read(tamanho_bloco)
            
            if bloco1 != bloco2:
                logging.error(f"Conteúdo diferente na posição {posicao}")
                # Mostra os primeiros bytes diferentes
                for i, (b1, b2) in enumerate(zip(bloco1[:32], bloco2[:32])):
                    if b1 != b2:
                        logging.error(f"Primeiro byte diferente na posição {posicao + i}: {b1} != {b2}")
                        break
                return False
                
            if not bloco1:
                break
                
            posicao += len(bloco1)
            if posicao % (1024 * 1024) == 0:  # Log a cada 1MB
                logging.info(f"Verificado: {posicao/tamanho1*100:.1f}%")
    
    logging.info("Verificação concluída com sucesso")
    return True

def testar_transferencia_paralela():
    """Testa a transferência paralela de um arquivo grande"""
    logging.info("=== Teste de Transferência Paralela ===")
    
    try:
        # Prepara ambiente
        for dir_name in ["peer1_files", "peer2_files"]:
            if os.path.exists(dir_name):
                shutil.rmtree(dir_name)
            os.makedirs(dir_name)
        
        # Cria arquivo grande para teste
        arquivo_teste = os.path.join("peer1_files", "arquivo_grande.dat")
        criar_arquivo_grande(arquivo_teste, tamanho_mb=20)  # 20MB
        
        # Verifica hash do arquivo original
        with open(arquivo_teste, 'rb') as f:
            hash_original = hashlib.sha256(f.read()).hexdigest()
            logging.info(f"Hash do arquivo original: {hash_original}")
        
        # Inicia tracker
        tracker = Tracker()
        thread_tracker = threading.Thread(target=tracker.iniciar)
        thread_tracker.daemon = True
        thread_tracker.start()
        time.sleep(1)
        
        # Cria peers
        peer1 = PeerClient(diretorio="peer1_files")
        peer2 = PeerClient(diretorio="peer2_files")
        
        # Conecta peers
        peer1.conectar_tracker("localhost", 55555)
        peer2.conectar_tracker("localhost", 55555)
        time.sleep(1)
        
        # Registra arquivo no peer1
        logging.info("Registrando arquivo no peer1")
        info_arquivo = peer1.adicionar_arquivo(arquivo_teste)
        logging.info(f"Arquivo registrado: {info_arquivo}")
        time.sleep(1)
        
        # Inicia transferência
        logging.info("\nIniciando transferência paralela")
        inicio = time.time()
        
        sucesso = peer2.solicitar_arquivo(peer1.peer_id, "arquivo_grande.dat")
        if not sucesso:
            logging.error("Falha ao iniciar transferência")
            return False
            
        # Aguarda conclusão (com timeout)
        arquivo_destino = os.path.join("peer2_files", "arquivo_grande.dat")
        timeout = 60  # 1 minuto
        while not os.path.exists(arquivo_destino) and (time.time() - inicio) < timeout:
            time.sleep(0.1)
        
        if not os.path.exists(arquivo_destino):
            logging.error("Arquivo não foi criado após timeout")
            return False
            
        tempo_total = time.time() - inicio
        
        # Verifica tamanho
        tamanho_original = os.path.getsize(arquivo_teste)
        tamanho_destino = os.path.getsize(arquivo_destino)
        logging.info(f"Tamanho original: {tamanho_original}")
        logging.info(f"Tamanho destino: {tamanho_destino}")
        
        # Verifica hash do arquivo transferido
        with open(arquivo_destino, 'rb') as f:
            hash_destino = hashlib.sha256(f.read()).hexdigest()
            logging.info(f"Hash do arquivo transferido: {hash_destino}")
        
        # Verifica resultado
        if verificar_arquivos(arquivo_teste, arquivo_destino):
            logging.info(f"\nTransferência concluída com sucesso em {tempo_total:.1f} segundos")
            tamanho_mb = os.path.getsize(arquivo_teste) / (1024 * 1024)
            velocidade = tamanho_mb / tempo_total
            logging.info(f"Velocidade média: {velocidade:.2f} MB/s")
            return True
        else:
            logging.error("Falha na verificação do arquivo")
            return False
            
    except Exception as e:
        logging.error(f"Erro durante teste: {str(e)}")
        logging.exception("Stack trace completo:")
        return False
    finally:
        # Limpa ambiente
        for dir_name in ["peer1_files", "peer2_files"]:
            if os.path.exists(dir_name):
                shutil.rmtree(dir_name)

def main():
    try:
        sucesso = testar_transferencia_paralela()
        logging.info("\n=== Resultado do Teste ===")
        logging.info("Transferência Paralela: " + ("OK" if sucesso else "Falha"))
    except KeyboardInterrupt:
        logging.info("\nTeste interrompido pelo usuário")
    except Exception as e:
        logging.error(f"Erro nos testes: {str(e)}")
        logging.exception("Stack trace completo:")

if __name__ == "__main__":
    main()