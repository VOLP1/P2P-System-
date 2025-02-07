# teste_incentivos.py
import os
import time
import logging
from tracker import Tracker
from peer_client import PeerClient
import threading

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def testar_incentivos():
    """Testa o sistema de incentivos"""
    logging.info("=== Teste do Sistema de Incentivos ===")
    
    try:
        # Inicializa ambiente
        for dir_name in ["peer1_files", "peer2_files","peer3_files"]:
            os.makedirs(dir_name, exist_ok=True)
        
        # Cria arquivo grande para teste
        with open("peer1_files/grande.txt", "w") as f:
            f.write("X" * (5 * 1024 * 1024))  # 5MB

        with open("peer3_files/grandes.txt", "w") as f:
            f.write("y" * (5 * 2024 * 1024))  # 5MB
        
        # Inicia tracker
        tracker = Tracker()
        thread_tracker = threading.Thread(target=tracker.iniciar)
        thread_tracker.daemon = True
        thread_tracker.start()
        time.sleep(1)
        
        # Cria peers
        peer1 = PeerClient(diretorio="peer1_files")
        peer2 = PeerClient(diretorio="peer2_files")
        peer3 = PeerClient(diretorio="peer3_files")
        
        # Conecta peers
        peer1.conectar_tracker("localhost", 55555)
        peer2.conectar_tracker("localhost", 55555)
        peer3.conectar_tracker("localhost", 55555)

        # Registra arquivo no peer1
        peer1.adicionar_arquivo("peer1_files/grande.txt")
        time.sleep(1)
        
        peer3.adicionar_arquivo("peer3_files/grandes.txt")
        time.sleep(1)

        # Verifica pontuação inicial
        logging.info("\nPontuações iniciais:")
        info1 = peer1._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer1.peer_id})
        info2 = peer2._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer2.peer_id})
        info3 = peer3._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer3.peer_id})
        logging.info(f"Peer 1: {info1.get('pontuacao', 0):.2f}")
        logging.info(f"Peer 2: {info2.get('pontuacao', 0):.2f}")
        logging.info(f"Peer 3: {info3.get('pontuacao', 0):.2f}")
        
        # Realiza transferências
        logging.info("\nIniciando transferências...")
        
        # peer2 baixa arquivo de peer1
        peer2.solicitar_arquivo(peer1.peer_id, "grande.txt")
        time.sleep(1)

        # peer2 baixa arquivo de peer1
        peer1.solicitar_arquivo(peer3.peer_id, "grandes.txt")
        time.sleep(1)
        
        # Verifica pontuações após transferência
        logging.info("\nPontuações após transferência:")
        info1 = peer1._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer1.peer_id})
        info2 = peer2._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer2.peer_id})
        info3 = peer3._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer3.peer_id})
        logging.info(f"Peer 1: {info1.get('pontuacao', 0):.2f}")
        logging.info(f"Peer 2: {info2.get('pontuacao', 0):.2f}")
        logging.info(f"Peer 3: {info3.get('pontuacao', 0):.2f}")
        
        # Espera mais um pouco para acumular tempo online
        time.sleep(5)
        
        # Verifica pontuações finais
        logging.info("\nPontuações finais:")
        info1 = peer1._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer1.peer_id})
        info2 = peer2._enviar_comando_tracker({"comando": "info_peer", "peer_id": peer2.peer_id})
        logging.info(f"Peer 1: {info1.get('pontuacao', 0):.2f}")
        logging.info(f"Peer 2: {info2.get('pontuacao', 0):.2f}")
        
    except Exception as e:
        logging.error(f"Erro durante teste: {str(e)}")
    finally:
        # Limpa ambiente
        for dir_name in ["peer1_files", "peer2_files"]:
            if os.path.exists(dir_name):
                for file in os.listdir(dir_name):
                    os.remove(os.path.join(dir_name, file))
                os.rmdir(dir_name)

if __name__ == "__main__":
    testar_incentivos()