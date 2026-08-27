"""Geliştirme sırasında uygulamayı çalıştırmak için kullanılacak giriş dosyası."""

from app import create_app


def main() -> None:
    """Start the local Flask development server."""
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
    main()
