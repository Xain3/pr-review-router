import argparse
import importlib.metadata

def main():
    parser = argparse.ArgumentParser(description='Command-line interface for pr-review-router.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {importlib.metadata.version("pr-review-router")}')
    
    # Add other commands here in the future

    args = parser.parse_args()

if __name__ == '__main__':
    main()