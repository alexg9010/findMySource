from .cli import main

if __name__ == "__main__":  # guard: worker processes (parallel parsing) re-import this module on macOS/Windows
    main()
