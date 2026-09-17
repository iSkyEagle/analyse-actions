import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
u = urlparse(os.environ["DATABASE_URL"])
print("utilisateur :", u.username)
print("hôte        :", u.hostname)
print("port        :", u.port)
print("mot de passe: ", "renseigné" if u.password else "ABSENT",
      "| caractères à encoder :", [c for c in (u.password or "") if c in "@:/?#[]%"])