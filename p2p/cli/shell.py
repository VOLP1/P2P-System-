import cmd
import sys
import os
import logging

# Adiciona o diretório pai ao path para importar os módulos
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(parent_dir)

from p2p.peer_client import PeerClient
from .logging_handler import setup_logging

class P2PClientShell(cmd.Cmd):
    """Interface de linha de comando para o cliente P2P"""
    intro = 'Bem-vindo ao Cliente P2P. Digite help ou ? para listar os comandos.'
    prompt = '(p2p) '
    
    def __init__(self, host, porta, diretorio):
        super().__init__()
        
        # Configura logging
        self.logger = setup_logging()
        
        # Inicializa o cliente
        self.cliente = PeerClient(diretorio=diretorio)
        
        # Conecta ao tracker
        if not self.cliente.conectar_tracker(host, porta):
            logging.error(f"Erro ao conectar ao tracker em {host}:{porta}")
            sys.exit(1)
        
        logging.info(f"Conectado como {self.cliente.peer_id}")

    def do_adicionar(self, arg):
        """Adiciona um arquivo para compartilhamento. 
        Uso: adicionar /caminho/para/arquivo"""
        if not arg:
            logging.warning("Por favor, forneça o caminho do arquivo.")
            return
        
        try:
            arquivo = self.cliente.adicionar_arquivo(arg)
            logging.info(f"Arquivo {arquivo.nome} adicionado com sucesso.")
        except Exception as e:
            logging.error(f"Erro ao adicionar arquivo: {e}")
    
    def do_buscar(self, arg):
        """Busca arquivos na rede. 
        Uso: buscar termo_de_busca"""
        if not arg:
            logging.warning("Por favor, forneça um termo de busca.")
            return
        
        try:
            resposta = self.cliente._enviar_comando_tracker({
                "comando": "buscar",
                "termo": arg
            })
            
            if resposta["status"] == "sucesso":
                resultados = resposta["resultados"]
                if not resultados:
                    logging.info("Nenhum arquivo encontrado.")
                    return
                
                print("\nArquivos encontrados:")
                for arquivo, peers in resultados.items():
                    print(f"\n{arquivo}:")
                    for peer in peers:
                        print(f"  - Peer: {peer['peer_id']} (Pontuação: {peer['pontuacao']:.2f})")
            else:
                logging.error(f"Erro na busca: {resposta.get('mensagem', 'Erro desconhecido')}")
        except Exception as e:
            logging.error(f"Erro ao buscar arquivos: {e}")
    
    def do_download(self, arg):
        """Baixa um arquivo da rede. 
        Uso: download nome_do_arquivo"""
        if not arg:
            logging.warning("Por favor, forneça o nome do arquivo.")
            return
        
        try:
            sucesso = self.cliente.solicitar_arquivo(arg)
            if sucesso:
                logging.info(f"Download de {arg} iniciado.")
            else:
                logging.error(f"Falha ao iniciar download de {arg}.")
        except Exception as e:
            logging.error(f"Erro ao baixar arquivo: {e}")
    
    def do_listar(self, arg):
        """Lista arquivos compartilhados localmente"""
        if not self.cliente.arquivos:
            print("Nenhum arquivo compartilhado.")
            return
        
        print("\nArquivos compartilhados:")
        for nome, arquivo in self.cliente.arquivos.items():
            print(f"- {nome} (Tamanho: {arquivo.tamanho} bytes)")
    
    def do_chat(self, arg):
        """Inicia um chat com outro peer. 
        Uso: chat peer_id"""
        if not arg:
            logging.warning("Por favor, forneça o peer_id.")
            return
        
        try:
            if self.cliente.iniciar_chat(arg):
                print(f"\nChat iniciado com {arg}")
                while True:
                    mensagem = input(f"Mensagem para {arg} (ou 'sair' para encerrar): ")
                    if mensagem.lower() == 'sair':
                        break
                    
                    if not self.cliente.enviar_mensagem_chat(arg, mensagem):
                        logging.error("Falha ao enviar mensagem.")
            else:
                logging.error(f"Falha ao iniciar chat com {arg}")
        except Exception as e:
            logging.error(f"Erro no chat: {e}")
    
    def do_sair(self, arg):
        """Encerra o cliente P2P"""
        print("\nEncerrando cliente...")
        return True
    
    def default(self, line):
        """Trata comandos não reconhecidos"""
        print(f"Comando não reconhecido: {line}. Digite 'help' para ver a lista de comandos.")