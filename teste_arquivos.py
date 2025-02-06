import time
import os
from tracker import Tracker
from peer_client import PeerClient
import threading

def main():
    # Cria diretórios para os peers
    if not os.path.exists("peer1_files"):
        os.makedirs("peer1_files")
    if not os.path.exists("peer2_files"):
        os.makedirs("peer2_files")
        
    # Cria um arquivo de teste para o peer1
    with open("peer1_files/teste.txt", "w") as f:
        f.write("Este é um arquivo de teste para transferência P2P!")

    # Inicializa o tracker em uma thread separada
    tracker = Tracker(host="localhost", porta=55555)
    thread_tracker = threading.Thread(target=tracker.iniciar)
    thread_tracker.daemon = True
    thread_tracker.start()
    
    # Aguarda o tracker inicializar
    time.sleep(1)
    
    # Cria dois peers com diretórios diferentes
    peer1 = PeerClient(host="localhost", diretorio_compartilhado="peer1_files")
    peer2 = PeerClient(host="localhost", diretorio_compartilhado="peer2_files")
    
    # Conecta os peers ao tracker
    peer1.conectar_tracker("localhost", 55555)
    peer2.conectar_tracker("localhost", 55555)
    
    # Aguarda os peers se registrarem
    time.sleep(2)
    
    print(f"Peer 1 ID: {peer1.peer_id}")
    print(f"Peer 2 ID: {peer2.peer_id}")
    
    # Adiciona arquivo ao peer1
    info_arquivo = peer1.adicionar_arquivo("peer1_files/teste.txt")
    print(f"Arquivo adicionado: {info_arquivo}")
    
    print("Tentando estabelecer conexão...")
    max_tentativas = 3
    for tentativa in range(max_tentativas):
        if peer1.iniciar_chat(peer2.peer_id):
            print("Conexão estabelecida!")
            
            print("Iniciando transferência do arquivo...")
            if peer2.solicitar_arquivo(peer1.peer_id, "teste.txt"):
                time.sleep(2)  # Aguarda a transferência
                
                if os.path.exists("peer2_files/teste.txt"):
                    with open("peer2_files/teste.txt", "r") as f:
                        conteudo = f.read()
                        print(f"Arquivo recebido com sucesso! Conteúdo:\n{conteudo}")
                else:
                    print("Erro: Arquivo não foi recebido!")
            break
        elif tentativa < max_tentativas - 1:
            print(f"Tentativa {tentativa + 1} falhou, tentando novamente...")
            time.sleep(1)
    
    # Mantém o programa rodando
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nEncerrando teste...")

if __name__ == "__main__":
    main()