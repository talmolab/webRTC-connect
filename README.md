# sleap-webRTC


## Description
This repo contains a DockerFile for a lightweight container (~6 GB) with the PyPI installation of SLEAP and all of its dependencies. The container repository is located at [https://hub.docker.com/repository/docker/eberrigan/sleap-cuda/general](https://hub.docker.com/repository/docker/eberrigan/sleap-cuda/general).

The base image used is [nvidia/cuda:11.3.1-cudnn8-runtime-ubuntu20.04](https://hub.docker.com/layers/nvidia/cuda/11.3.1-cudnn8-runtime-ubuntu20.04/images/sha256-025a321d3131b688f4ac09d80e9af6221f2d1568b4f9ea6e45a698beebb439c0).
- The Dockerfile is located at `./sleap_webRTC/webRTC_worker_container/Dockerfile`.
- The repo has CI set up in `.github/workflows` for building and pushing the image when making changes.
  - The workflow uses the linux/amd64 platform to build. 
- `.devcontainer/devcontainer.json` is convenient for developing inside a container made with the DockerFile using Visual Studio Code.
- Test data for training is located in `tests/sleap_webRTC`.


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

- Use the `devcontainer.json` to open the repo in a dev container using VS Code.
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
