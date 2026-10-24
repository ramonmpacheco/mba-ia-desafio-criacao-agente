import argparse

from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="Assistente do Residencial Aurora")
    parser.add_argument("command", choices=["serve", "reset"])
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == "reset":
        # Restauração deliberada do estado local. Execute com a API parada.
        for name in ["condominio.db", "sessoes.db"]:
            for suffix in ["", "-wal", "-shm"]:
                (settings.state_dir / (name + suffix)).unlink(missing_ok=True)
        from .storage import Store

        Store(settings)
        print("Reservas e visitantes restaurados; sessões e confirmações removidas.")
    else:
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=8000)


if __name__ == "__main__":
    main()
