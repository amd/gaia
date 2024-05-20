# GAIA
<img src="https://github.com/aigdat/gaia/assets/4722733/0db60b9b-05d5-4732-a74e-f67bc9bdb61b" alt="gaia" width="500">

## AIG Demo Hub

AIG demo hub is the interface that allows you to interface with GAIA agents (e.g. Neo and Datalin).

To install it, download and run the `AIG-Demo-Hub-4.14.1-windows-setup.exe` setup file.

<img src="https://github.com/aigdat/gaia/raw/main/img/demo_hub_1.png"  width="500"/>

<img src="https://github.com/aigdat/gaia/raw/main/img/demo_hub_2.png"  width="500"/>

## Initial setup
You will need to clone two repositories and follow directions there.
* git clone 

## Agents

### Neo

Neo is an agent designed to help you setup and play with GitHub repos.

To run Neo simply run the steps below:
* `conda create -n gaia python=3.9`
* `conda activate gaia`
* `pip install -r requirements.txt --upgrade`
* `pip install -r agents/Neo/requirements.txt --upgrade`
* 
* `python run.py`
* Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:3978/api/messages`

### Datalin

Datalin is an agent designed to help you reason about ML models using some of DAT tools.

To run Datalin simply run the steps below:
* `conda create -n gaia python=3.10`
* `conda activate gaia`
* `pip install -r agents/Datalin/requirements.txt`
* `python agents/Datalin/app.py`
* Open `AIG Demo Hub`, click on `Open Demo` and use `http://localhost:3978/api/messages`

### Create your own agent

You can learn how to create your own agent by following [those instructions](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-create-bot).
