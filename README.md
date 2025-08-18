# Position Liquidation Tool
## 1. First run
### 1.1. Install Docker for your OS
You can download Docker [here](https://docs.docker.com/engine/install/ubuntu/)</br>
Just choose your OS and follow the  instructions there to install it.<br>
Make sure you have both Docker and Docker Compose installed. If you don’t, go ahead and set them up. To check if they’re installed, run these commands in your terminal:
```bash
docker -v
docker compose version
```

### 1.2. Clone the repository
```bash
git clone <REPO_URL>
cd <REPO_NAME>
```
### 1.3. Make environment file
Near the `docker-compose.yml` file, create a `.env` file with the following content:
You should add ETH to wallet address.
```env
POSTGRES_USER=<DB_USER>
POSTGRES_PASSWORD=<DB_PASSWORD>
POSTGRES_PORT=<DB_PORT>
POSTGRES_DB=<DB_NAME>
PRIVATE_KEY=<PRIVET_KEY_YOU_WALLET_ADDRESS>
MARGIN_MODULE_ADDRESS=<MARGIN_MODULE_ADDRESS>
HTTP_RPC_URL=<RPC_URL>
```

### 1.3. Build docker image and run the container
```bash
docker compose -f docker-compose.yml --build up -d
```


## 2. Help commands
Check logs
```bash
docker compose -f docker-compose.yaml logs -f tracker
```
Drop db
```bash
docker compose -f docker-compose.yml down -v
```

## 3. Update Margin Module Address
If you want to update the Margin Module Address, you can do it by running the following next steps
Change the `MARGIN_MODULE_ADDRESS` in the `.env` file to the new address<br>
You should down old data in the database
```bash
docker compose -f docker-compose.yml down -v
```
And run command `1.3`



