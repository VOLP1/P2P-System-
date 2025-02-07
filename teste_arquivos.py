import time
import os
import logging
import shutil
from tracker import Tracker
from peer_client import PeerClient
import threading

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def limpar_diretorios():
    """Limpa os diretórios de teste"""
    for dir_name in ["peer1_files", "peer2_files"]:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)

def criar_diretorios():
    """Cria os diretórios necessários para o teste"""
    for dir_name in ["peer1_files", "peer2_files"]:
        try:
            os.makedirs(dir_name, exist_ok=True)
            if not os.access(dir_name, os.W_OK):
                raise PermissionError(f"Sem permissão de escrita no diretório {dir_name}")
        except Exception as e:
            logging.error(f"Erro ao criar diretório {dir_name}: {str(e)}")
            raise

def criar_arquivo_teste():
    """Cria um arquivo de teste com conteúdo conhecido"""
    try:
        with open("peer1_files/teste.txt", "w") as f:
            f.write("Este é um arquivo de teste para transferência P2P!\n")
            f.write("Segunda linha para teste.\n")
            f.write("Terceira linha para confirmar a transferência completa.")
    except Exception as e:
        logging.error(f"Erro ao criar arquivo de teste: {str(e)}")
        raise

def verificar_transferencia(arquivo_origem: str, arquivo_destino: str) -> bool:
    """
    Verifica se a transferência foi bem sucedida comparando os arquivos
    
    Args:
        arquivo_origem: Caminho do arquivo original
        arquivo_destino: Caminho do arquivo recebido
        
    Returns:
        bool: True se os arquivos são idênticos
    """
    try:
        if not os.path.exists(arquivo_destino):
            logging.error(f"Arquivo de destino não existe: {arquivo_destino}")
            return False
            
        with open(arquivo_origem, 'rb') as f1, open(arquivo_destino, 'rb') as f2:
            conteudo_origem = f1.read()
            conteudo_destino = f2.read()
            
        if conteudo_origem == conteudo_destino:
            logging.info("Verificação de conteúdo: OK")
            return True
        else:
            logging.error("Conteúdo dos arquivos difere")
            return False
            
    except Exception as e:
        logging.error(f"Erro ao verificar arquivos: {str(e)}")
        return False

def main():
    try:
        # Limpa e recria os diretórios de teste
        logging.info("Preparando ambiente de teste...")
        limpar_diretorios()
        criar_diretorios()
        criar_arquivo_teste()
        
        # Inicializa o tracker em uma thread separada
        logging.info("Iniciando tracker...")
        tracker = Tracker(host="localhost", porta=55555)
        thread_tracker = threading.Thread(target=tracker.iniciar)
        thread_tracker.daemon = True
        thread_tracker.start()
        
        # Aguarda o tracker inicializar
        time.sleep(1)
        
        # Cria os peers
        logging.info("Criando peers...")
        peer1 = PeerClient(host="localhost", diretorio_compartilhado="peer1_files")
        peer2 = PeerClient(host="localhost", diretorio_compartilhado="peer2_files")
        
        # Conecta os peers ao tracker
        logging.info("Conectando peers ao tracker...")
        if not peer1.conectar_tracker("localhost", 55555):
            raise Exception("Falha ao conectar peer1 ao tracker")
        if not peer2.conectar_tracker("localhost", 55555):
            raise Exception("Falha ao conectar peer2 ao tracker")
        
        # Aguarda os peers se registrarem
        time.sleep(2)
        
        logging.info(f"Peer 1 ID: {peer1.peer_id}")
        logging.info(f"Peer 2 ID: {peer2.peer_id}")
        
        # Adiciona arquivo ao peer1
        logging.info("Adicionando arquivo ao peer1...")
        info_arquivo = peer1.adicionar_arquivo("peer1_files/teste.txt")
        logging.info(f"Arquivo adicionado: {info_arquivo}")
        
        # Tenta estabelecer conexão e transferir arquivo
        logging.info("Iniciando teste de transferência...")
        max_tentativas = 3
        sucesso = False
        
        for tentativa in range(max_tentativas):
            logging.info(f"Tentativa {tentativa + 1} de {max_tentativas}")
            
            if peer1.iniciar_chat(peer2.peer_id):
                logging.info("Conexão estabelecida!")
                
                logging.info("Iniciando transferência do arquivo...")
                if peer2.solicitar_arquivo(peer1.peer_id, "teste.txt", timeout=30):
                    # Aguarda a transferência completar
                    time.sleep(2)
                    
                    # Verifica se a transferência foi bem sucedida
                    if verificar_transferencia(
                        "peer1_files/teste.txt",
                        "peer2_files/teste.txt"
                    ):
                        logging.info("Transferência concluída com sucesso!")
                        with open("peer2_files/teste.txt", "r") as f:
                            logging.info(f"Conteúdo do arquivo recebido:\n{f.read()}")
                        sucesso = True
                        break
                    else:
                        logging.error("Verificação da transferência falhou")
                else:
                    logging.error("Falha ao solicitar arquivo")
            
            if tentativa < max_tentativas - 1:
                logging.info("Aguardando antes de tentar novamente...")
                time.sleep(2)
        
        if not sucesso:
            logging.error("Todas as tentativas de transferência falharam")
            
        # Mantém o programa rodando para inspeção
        logging.info("Teste concluído. Pressione Ctrl+C para encerrar...")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logging.info("\nEncerrando teste...")
    except Exception as e:
        logging.error(f"Erro durante o teste: {str(e)}")
        raise
    finally:
        logging.info("Limpando ambiente de teste...")
        limpar_diretorios()

if __name__ == "__main__":
    main()