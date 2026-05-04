python -m pip install -r requirements.txt

python -m uvicorn app.main:app --reload
python -m uvicorn app.main:app --reload --port 8080

http://localhost:8000

http://localhost:8000/docs