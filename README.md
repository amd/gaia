# GAIA
<img src="https://github.com/aigdat/gaia/assets/4722733/0db60b9b-05d5-4732-a74e-f67bc9bdb61b" alt="gaia" width="500">

## AIG Demo Hub

AIG demo hub is the interface that allows you to interface with GAIA agents (e.g. Neo and Datalin).

To install it, run the `AIG-Demo-Hub-4.14.1-windows-setup.exe` setup file in the repo.

<img src="https://github.com/aigdat/gaia/raw/main/img/demo_hub_1.png"  width="500"/>

<img src="https://github.com/aigdat/gaia/raw/main/img/demo_hub_2.png"  width="500"/>

## Running Agents
To run an agent, simply run the steps below:
* `conda create -n gaia python=3.9`
* `conda activate gaia`
* `pip install -r requirements.txt --upgrade`
* `pip install -r requirements_pytorch.txt --upgrade`
* `pip install -r ./agents/<agent_name>/requirements.txt`
* `python run.py`
* Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:3978/api/messages`
* NOTE: each agent is hosted on a separate port, connect the desired agent by modifying the target port above.

## Running RyzenAI NPU Web Server
To get setup initially, you will need to setup the Ryzen AI NPU web server by following the directions below.
* clone the lemonade repo, which is used for hosting LLMs via a web server: `git clone https://github.com/aigdat/genai.git`
* follow directions for setup and running the Ryzen AI NPU described (here)[https://github.com/aigdat/genai/blob/main/docs/easy_ryzenai_npu.md]
* `conda activate ryzenai-transformers`
* run `setup.bat`
* run `start_npu_server`
You should see an output similar to the one below:
```
Info: Running tool: serve
INFO:     Started server process [18836]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     ('::1', 50649) - "WebSocket /ws" [accepted]
INFO:     connection open
```
NOTE: use command shell only, not powershell.

## Running RyzenAI iGPU Web Server
To get setup initially, you will need to setup the Ryzen AI iGPU web server by following the directions below.
* clone the lemonade repo, which is used for hosting LLMs via a web server: `git clone https://github.com/aigdat/genai.git`
* follow directions for setup and running the Ryzen AI iGPU described (here)[https://github.com/aigdat/genai/blob/main/docs/ort_genai.md]
* run `start_igpu_server`

### Create your own agent
You can learn how to create your own agent by following [those instructions](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-create-bot).