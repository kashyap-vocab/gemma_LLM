# LiveKit Agents

## Setup

1. Clone the repository
2. Copy `.env.sample` to `.env` and fill in the required values
3. Create virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

### Run agent locally on console to test

```bash
python agent.py console
```

### Run agent on development server for telephonic channel
1. Start the agent
```bash
python agent.py dev
```
2. Run the smartflow script
```bash
python smartflow.py
```
3. Use ngrok to expose the development server to the internet
```bash
ngrok http 8319
```


### Note: If the script python agent.py dev is not working, try 
```bash
python agent.py download-files
python agent.py dev
```