import sys
print("Executable:", sys.executable)
try:
    from app.main import app
    print("App imported successfully")
except Exception as e:
    import traceback
    traceback.print_exc()
