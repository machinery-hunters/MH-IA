import argparse

from app.ingestion.indexer import Indexer


def main() -> None:
    parser = argparse.ArgumentParser(description="Sincroniza Google Drive -> Qdrant")
    parser.add_argument(
        "--folder-id",
        default=None,
        help="ID de carpeta de Drive (default: GOOGLE_DRIVE_FOLDER_ID en .env)",
    )
    args = parser.parse_args()

    Indexer().sync(folder_id=args.folder_id)


if __name__ == "__main__":
    main()
