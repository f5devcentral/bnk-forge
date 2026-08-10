docker compose exec -it backend python -c "
from database import SessionLocal
from models.bare_metal import BareMetalHost
from models.ssh_credential import SSHCredential
from core.encryption import decrypt_value
db = SessionLocal()
for h in db.query(BareMetalHost).all():
    cred = h.dpu_credential
    if cred and cred.password_encrypted:
        print(f'Host: {h.hostname} (id={h.id})')
        print(f'  DPU username: {cred.username}')
        print(f'  DPU password: {decrypt_value(cred.password_encrypted)}')
    else:
        print(f'Host: {h.hostname} (id={h.id}) — no DPU credential')
db.close()
"
