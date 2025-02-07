# testes.py
import os
import time
import logging
import shutil
from tracker import Tracker
from peer_client import PeerClient
import threading

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def limpar_diretorios():
    """Limpa os diretórios de teste"""
    for dir_name in ["peer1_files", "peer2_files"]:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
        os.makedirs(dir_name)

def testar_conexao_tracker():
    """Teste 1: Verifica conexão com tracker"""
    logging.info("=== Teste de Conexão com Tracker ===")
    
    peer1 = PeerClient(diretorio="peer1_files")
    peer2 = PeerClient(diretorio="peer2_files")
    
    sucesso1 = peer1.conectar_tracker("localhost", 55555)
    sucesso2 = peer2.conectar_tracker("localhost", 55555)
    
    if sucesso1 and sucesso2:
        logging.info(f"Peer 1 conectado com ID: {peer1.peer_id}")
        logging.info(f"Peer 2 conectado com ID: {peer2.peer_id}")
        return peer1, peer2
    else:
        logging.error("Falha na conexão com tracker")
        return None, None

def testar_chat(peer1, peer2):
    """Teste 2: Verifica chat entre peers"""
    logging.info("\n=== Teste de Chat ===")
    
    # Tenta estabelecer conexão de chat
    if peer1.iniciar_chat(peer2.peer_id):
        logging.info("Conexão de chat estabelecida")
        time.sleep(1)
        
        # Tenta enviar mensagens
        if peer1.enviar_mensagem_chat(peer2.peer_id, "Olá do peer 1!"):
            logging.info("Mensagem 1 enviada com sucesso")
            time.sleep(1)
            
            if peer2.enviar_mensagem_chat(peer1.peer_id, "Olá do peer 2!"):
                logging.info("Mensagem 2 enviada com sucesso")
                return True
    
    logging.error("Falha no teste de chat")
    return False

def testar_transferencia_arquivo(peer1, peer2):
    """Teste 3: Verifica transferência de arquivos"""
    logging.info("\n=== Teste de Transferência de Arquivo ===")
    
    # Cria arquivo de teste
    arquivo_teste = os.path.join("peer1_files", "teste.txt")
    with open(arquivo_teste, "w") as f:
        f.write("Teste de transferência de arquivo P2P\n")
        f.write("Segunda linha para teste\n")
        f.write("Terceira linha para teste\n")
    
    # Adiciona arquivo ao peer1
    info = peer1.adicionar_arquivo(arquivo_teste)
    logging.info(f"Arquivo adicionado ao peer1: {info}")
    time.sleep(1)
    
    # Tenta transferir para peer2
    if peer2.solicitar_arquivo(peer1.peer_id, "teste.txt"):
        # Verifica se arquivo foi recebido
        arquivo_recebido = os.path.join("peer2_files", "teste.txt")
        if os.path.exists(arquivo_recebido):
            with open(arquivo_recebido, 'r') as f:
                conteudo = f.read()
                logging.info(f"Arquivo recebido com conteúdo:\n{conteudo}")
            return True
    
    logging.error("Falha na transferência do arquivo")
    return False

def testar_busca(peer1, peer2):
    """Teste 4: Verifica busca de arquivos"""
    logging.info("\n=== Teste de Busca de Arquivos ===")
    
    # Cria mais arquivos para teste
    arquivos = {
        "documento1.txt": "Conteúdo do documento 1",
        "foto.jpg": "Simulação de foto",
        "video.mp4": "Simulação de vídeo",
        "documento2.txt": "Conteúdo do documento 2"
    }
    
    # Distribui arquivos entre os peers
    for nome, conteudo in arquivos.items():
        caminho = os.path.join("peer1_files" if len(nome) % 2 == 0 else "peer2_files", nome)
        with open(caminho, "w") as f:
            f.write(conteudo)
        if len(nome) % 2 == 0:
            peer1.adicionar_arquivo(caminho)
        else:
            peer2.adicionar_arquivo(caminho)
    
    time.sleep(1)  # Aguarda atualização no tracker
    
    # Testa busca
    termos_teste = [
        ("documento", 2),  # termo, número esperado de resultados
        ("foto", 1),
        ("video", 1),
        ("xyz", 0)
    ]
    
    sucesso = True
    for termo, esperado in termos_teste:
        logging.info(f"\nBuscando por '{termo}'...")
        resultados = peer1.buscar_arquivos(termo)
        encontrados = len(resultados)
        
        if encontrados == esperado:
            logging.info(f"OK - Encontrados {encontrados} arquivo(s)")
            for arquivo, peers in resultados.items():
                logging.info(f"- {arquivo} (disponível em {len(peers)} peers)")
        else:
            logging.error(f"Erro - Esperados {esperado}, encontrados {encontrados}")
            sucesso = False
    
    return sucesso

def main():
    try:
        # Prepara ambiente
        limpar_diretorios()
        
        # Inicia tracker
        tracker = Tracker()
        thread_tracker = threading.Thread(target=tracker.iniciar)
        thread_tracker.daemon = True
        thread_tracker.start()
        time.sleep(1)
        
        # Executa testes
        # Executa testes
        peer1, peer2 = testar_conexao_tracker()
        if not peer1 or not peer2:
            return

        # Executa todos os testes
        chat_ok = testar_chat(peer1, peer2)
        time.sleep(1)  # Aguarda entre testes
        
        transfer_ok = testar_transferencia_arquivo(peer1, peer2)
        time.sleep(1)  # Aguarda entre testes
        
        busca_ok = testar_busca(peer1, peer2)
        
        # Resultado final
        logging.info("\n=== Resultado dos Testes ===")
        logging.info(f"Conexão Tracker: OK")
        logging.info(f"Chat: {'OK' if chat_ok else 'Falha'}")
        logging.info(f"Transferência: {'OK' if transfer_ok else 'Falha'}")
        logging.info(f"Busca: {'OK' if busca_ok else 'Falha'}")
        
    except Exception as e:
        logging.error(f"Erro durante os testes: {str(e)}")
        logging.debug("Erro detalhado:", exc_info=True)
    finally:
        logging.info("\nLimpando ambiente de teste...")
        limpar_diretorios()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Testes do Sistema P2P')
    parser.add_argument('--test', choices=['all', 'chat', 'transfer', 'search'],
                       default='all', help='Teste específico para executar')
    args = parser.parse_args()
    
    try:
        # Prepara ambiente
        limpar_diretorios()
        
        # Inicia tracker
        tracker = Tracker()
        thread_tracker = threading.Thread(target=tracker.iniciar)
        thread_tracker.daemon = True
        thread_tracker.start()
        time.sleep(1)
        
        # Inicializa peers
        peer1, peer2 = testar_conexao_tracker()
        if not peer1 or not peer2:
            logging.error("Falha ao inicializar peers")
            exit(1)
        
        # Executa teste(s) selecionado(s)
        if args.test in ['all', 'chat']:
            testar_chat(peer1, peer2)
            
        if args.test in ['all', 'transfer']:
            testar_transferencia_arquivo(peer1, peer2)
            
        if args.test in ['all', 'search']:
            testar_busca(peer1, peer2)
            
    except KeyboardInterrupt:
        logging.info("\nTestes interrompidos pelo usuário")
    except Exception as e:
        logging.error(f"Erro nos testes: {str(e)}")
        logging.debug("Erro detalhado:", exc_info=True)
    finally:
        logging.info("Limpando ambiente...")
        limpar_diretorios()