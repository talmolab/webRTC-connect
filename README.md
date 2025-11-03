# sleap-webRTC


## Description
This repo contains three DockerFiles for lightweight containers (~8 GB) corresponding to the Worker, SLEAP Worker, and Signaling Server. The container repository is located at [https://hub.docker.com/repository/docker/eberrigan/sleap-cuda/general](https://hub.docker.com/repository/docker/eberrigan/sleap-cuda/general).

The base image used is [python-3.9-slim](https://hub.docker.com/layers/library/python/3.9/images/sha256-8806b78efc2b334c3f6231ae21c43b029c1ae6ab56bdb6a4b95e58bbce85899a) for the Worker. For the SLEAP Worker, the base image is [ghcr.io/talmolab/sleap-cuda:linux-amd64-2a8cefd7d0291d2ce07998fa3c54c9d0d618d31b](ghcr.io/talmolab/sleap-cuda:linux-amd64-2a8cefd7d0291d2ce07998fa3c54c9d0d618d31b)
- The Worker Dockerfile is located at `./webRTC_worker_container/Dockerfile`.
- The SLEAP Worker Dockerfile is located at `./webRTC_worker_sleap_container/Dockerfile`
- The Signaling Server Dockerfile is located at `./webRTC_external/Dockerfile`.
- The repo has CI set up in `.github/workflows` for building and pushing the image when making changes.
  - The workflow uses the linux/amd64 & linux/arm64 platform to build. 
- `.devcontainer/devcontainer.json` is convenient for developing inside a container made with the DockerFile using Visual Studio Code.

Currently, the Signaling Server is run on a single AWS EC2 node inside a container such that both the Worker/SLEAP Worker (inside its own separate container) and the Client (outside both containers) can register with this Signaling Server and discover each other to initate simple text messaging and file sharing. The SLEAP Worker for the webRTC MVP Demo is also capable of using the received files for training, as it is hosted on the Salk GPU cluster. 


## Installation

**Make sure to have Docker Daemon running first**


You can pull the image if you don't have it built locally, or need to update the latest, with

```
docker pull eberrigan/sleap-cuda:latest
```

## Usage 

Then, to run the image with gpus interactively:

```
docker run --gpus all -it eberrigan/sleap-cuda:latest bash
```

and test with 

```
python -c "import sleap; sleap.versions()" && nvidia-smi
```

In general, use the syntax

```
docker run -v /path/on/host:/path/in/container [other options] image_name [command]
```

Note that host paths are absolute. 


Use this syntax to give host permissions to mounted volumes
```
docker run -u $(id -u):$(id -g) -v /your/host/directory:/container/directory [options] your-image-name [command]
```

```
docker run -u $(id -u):$(id -g) -v ./tests/data:/tests/data --gpus all -it eberrigan/sleap-cuda:latest bash
```

Test:

```
 python3 -c "import sleap; print('SLEAP version:', sleap.__version__)"
 nvidia-smi # Check that the GPUs are discoverable
 sleap-train "tests/data/initial_config.json" "tests/data/dance.mp4.labels.slp" --video-paths "tests/data/dance.mp4"
```

**Notes:**

- The `eberrigan/sleap-cuda` is the Docker registry where the images are pulled from. This is only used when pulling images from the cloud, and not necesary when building/running locally.
- `-it` ensures that you get an interactive terminal. The `i` stands for interactive, and `t` allocates a pseudo-TTY, which is what allows you to interact with the bash shell inside the container.
- The `-v` or `--volume` option mounts the specified directory with the same level of access as the directory has on the host.
- `bash` is the command that gets executed inside the container, which in this case is to start the bash shell.
- Order of operations is 1. Pull (if needed): Get a pre-built image from a registry. 2. Run: Start a container from an image.

## Contributing

- Use the `devcontainer.json` to open the repo in a dev container using VS Code for the Worker and Signaling Server separately.
  - There is some test data in the `tests` directory that will be automatically mounted for use since the working directory is the workspace.
  - Rebuild the container when you make changes using `Dev Container: Rebuild Container`.

- Please make a new branch, starting with your name, with any changes, and request a reviewer before merging with the main branch since this image will be used by others.
- Please document using the same conventions (docstrings for each function and class, typing-hints, informative comments).
- Tests are written in the pytest framework. Data used in the tests are defined as fixtures in `tests/fixtures/data.py` (https://docs.pytest.org/en/6.2.x/reference.html#fixtures-api).


## Build
To build and push via automated CI, just push changes to a branch. 
- Pushes to `main` result in an image with the tag `latest`. 
- Pushes to other branches have tags with `-test` appended. 
- See `.github/workflows` for testing and production workflows.

To test `test` images locally use after pushing the `test` images via CI:

```
docker pull eberrigan/sleap-cuda:linux-amd64-test
```

then 

```
docker run -v ./tests/data:/tests/data --gpus all -it eberrigan/sleap-cuda:linux-amd64-test bash
```

To build locally for testing you can use the command (from the root of the repo):

```
docker build --platform linux/amd64 ./sleap_cuda
```

## Infrastructure Deployment

The signaling server can be deployed to AWS using Terraform for automated, reproducible infrastructure provisioning.

### Key Features
- **Elastic IP**: Stable IP address that persists across instance replacements
- **Automated startup**: Docker container starts automatically on instance boot
- **Multi-environment**: Separate configurations for dev, staging, and production
- **Security**: Configurable network access controls and IAM roles
- **Health checks**: Automated monitoring with automatic container restart

### Quick Start

1. Navigate to the Terraform directory:
   ```bash
   cd terraform/environments/dev  # or production
   ```

2. Configure variables:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with your values
   ```

3. Deploy:
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```

4. Get connection details:
   ```bash
   terraform output
   ```

For complete deployment instructions, troubleshooting, and cost estimates, see [terraform/README.md](terraform/README.md).

## Support
contact Elizabeth at eberrigan@salk.edu


## Temp 
1. start the server
   - mamba env create -f env.yaml to make conda env for the first time
   - mamba activate sleap-webrtc to activate conda env every time
   - python3 sleap_webRTC\webRTC_external\server.py
2. start the worker in the container
   - make sure to have docker desktop running on computer and signed in
   - visit https://github.com/talmolab/sleap-cuda-container/pkgs/container/sleap-webrtc for latest docker image to pull the test image
   - docker pull ghcr.io/talmolab/sleap-webrtc:linux-amd64-99025482bab7ecd1ac7859f8eff1773738c5a17e-test (git hash changes each time we modify the image)
   - docker run -it ghcr.io/talmolab/sleap-webrtc:linux-amd64-99025482bab7ecd1ac7859f8eff1773738c5a17e-test bash to run interactive container with bash
   - python3 /app/worker.py
3. start the client
   - new terminal
   - mamba activate sleap-webrtc to activate conda env every time
   - python3 sleap_webRTC\webRTC_external\client.py
4. send messages between client and worker
   - "quit" to exit
