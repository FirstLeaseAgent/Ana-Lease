import os
from pathlib import Path
import psycopg

def main():
    # Run only against the new AnaLease DATABASE_URL wired in render.yaml.
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        with conn.cursor() as cur:
            cur.execute(Path(__file__).with_name('schema.sql').read_text())

if __name__ == '__main__':
    main()
