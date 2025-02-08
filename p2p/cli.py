import argparse
import sys
from cli.shell import P2PClientShell

def main():
    # Configuração de argumentos de linha de comando
    parser = argparse.ArgumentParser(description='Cliente P2P')
    parser.add_argument('--host', default='localhost', help='Host do tracker')
    parser.add_argument('--porta', type=int, default=55555, help='Porta do tracker')
    parser.add_argument('--diretorio', default='compartilhado', help='Diretório de arquivos')
    args = parser.parse_args()
    
    # Inicia o shell interativo
    try:
        shell = P2PClientShell(args.host, args.porta, args.diretorio)
        shell.cmdloop()
    except KeyboardInterrupt:
        print("\nCliente encerrado pelo usuário.")
    except Exception as e:
        print(f"Erro ao iniciar cliente: {e}")

if __name__ == "__main__":
    main()