import time
from tracker import Tracker
from peer_client import PeerClient
import threading

def main():
    # Inicializa o tracker em uma thread separada
    tracker = Tracker(host="localhost", porta=55555)
    thread_tracker = threading.Thread(target=tracker.iniciar)
    thread_tracker.daemon = True
    thread_tracker.start()
    
    # Aguarda o tracker inicializar
    time.sleep(1)
    
    # Cria dois peers
    peer1 = PeerClient(host="localhost")
    peer2 = PeerClient(host="localhost")
    
    # Conecta os peers ao tracker
    peer1.conectar_tracker("localhost", 55555)
    peer2.conectar_tracker("localhost", 55555)
    
    # Aguarda os peers se registrarem
    time.sleep(1)
    
    print(f"Peer 1 ID: {peer1.peer_id}")
    print(f"Peer 2 ID: {peer2.peer_id}")
    
    # Dá tempo para os servidores dos peers iniciarem
    time.sleep(2)

    # Tenta iniciar chat algumas vezes (com retry)
    max_tentativas = 3
    for tentativa in range(max_tentativas):
        if peer1.iniciar_chat(peer2.peer_id):
            print("Chat iniciado com sucesso!")
            break
        elif tentativa < max_tentativas - 1:
            print(f"Tentativa {tentativa + 1} falhou, tentando novamente...")
            time.sleep(1)
        else:
            print("Não foi possível estabelecer o chat após várias tentativas")
    
    # Testa o envio de mensagens
    peer1.enviar_mensagem_chat(peer2.peer_id, "Olá do Peer 1!")
    time.sleep(1)
    peer2.enviar_mensagem_chat(peer1.peer_id, "Olá do Peer 2!")
    
    # Mantém o programa rodando
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nEncerrando teste...")

if __name__ == "__main__":
    main()