import sys
from .compile import main, BuildError

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BuildError as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        sys.exit(1)
