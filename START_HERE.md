# 🚀 Start Here - BRD to WBS Pipeline

Your test failed because the **API server wasn't running**. Here's how to fix it:

## Quick Fix (2 steps)

### 1. Start the Server

```bash
python start_server.py
```

This will:
- ✓ Check Redis & PostgreSQL are running
- ✓ Validate environment variables
- ✓ Run database migrations
- ✓ Start API server at http://localhost:8000

### 2. Run the Test (in a new terminal)

```bash
python test_brd_pipeline.py
```

---

## If Services Aren't Running

The startup script will tell you. Start them with:

```bash
# Redis
docker run -d --name redis -p 6379:6379 redis:latest

# PostgreSQL
docker run -d --name postgres -p 5432:5432 \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=aidevplatform \
  postgres:15
```

Then run `python start_server.py` again.

---

## Files Created for You

- **`start_server.py`** - Smart startup script (checks everything)
- **`test_brd_pipeline.py`** - Full pipeline test
- **`check_services.bat`** - Check if services are running (Windows)
- **`check_services.sh`** - Check if services are running (Linux/Mac)
- **`QUICK_TEST.md`** - Quick reference guide

---

## Need Help?

See detailed instructions in:
- `QUICK_TEST.md` - Quick troubleshooting
- `TEST_BRD_TO_WBS.md` - Complete testing guide

---

**Next:** Run `python start_server.py` in this terminal, then open a new terminal and run `python test_brd_pipeline.py`
