# Root JupyterLab Images
This repository contains the Docker build files for the JupyterLab images that are part of the Root integrated analytics platform. The container images package dependencies and libraries for computer vision, machine learning, and other Data Science work.

This repository is stuctured in a series of layers. Images at the bottom provide light-weight Spark and Dask executors, images in the middle provide components for running headless Spark driver applications within a Kubernetes environment, and images at the top provide Jupyter and high-level libraries for interactively working with data.

Core Spark:

* `Dockerfile.k8s-executor`: core foundation image for Root environment. Provides a minimal Spark environment with Python, Scala, and Rust environments. It also includes the dependencies needed to work with files stored in Amazon S3 or MinIO (via the `s3a` storage driver for Spark). 
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-base`
	- Parent: `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04`
	- Docker Hub Tag: `oaktreetech/root.base`
* `Dockerfile.k8s-driver`: extension of the Spark executor image that provides additional components, such as `kubectl` so that the image can be used to run headless driver components on a Kubernetes cluster.
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-driver`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-base`
	- Docker Hub Tag: `oaktreetech/root.spark-driver`

_For details on the design philosophy and discussion of how and why the images were created, see [Spark on Kubernetes: First Steps](https://oak-tree.tech/blog/spark-kubernetes-jupyter). **Note (2026-0129)**: The JupyterLab article was first published in 2019 and many of the particulars of this environment have changed (such as using `pip` to install PySpark and reducing the number of kernels to focus on Python, Scala, and Rust). This refernence is historical though still useful for understanding the project's aims/goals._

Jupyter and general Data Science:

* `Dockerfile.k8s-jupyter`: minimal Jupyter image that provdes the core components of the Scientific Python stack: NumPy, Pandas, Matplotlib, Seaborn, Bokeh, SciPy, and Sonador client.
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-jupyter`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-driver`
	- Docker Hub Tag: `oaktreetech/root.jupyterlab-singleuser`
* `Dockerfile.hub-jupyter`: JupyterLab Python image used in Oak-Tree Root deployments with a broad a broad set of data processing libraries for Big Data Analytics, Data Visualization, Geographic Information Systems, and Medical Informatics. _Default image used for Sonador analytics environment._
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-analytics-core:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-minio-jupyter`
	- Docker Hub Tag: `oaktreetech/root.analytics-core`
* `Dockerfile.k8s-dl`: Deep Learning image that includes NVIDIA drivers, CUDA utilities, TensorFlow, and PyTorch. 
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-dl:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-analytics-core:latest`
	- Docker Hub Tag: `oaktreetech/root.dl-light`
* `Dockerfile.hub-polyglot`: Extension of the JupyterHub image that provides Python, Scala/Java, and R kernels. _Midsize appropriate for deployment as a cloud analytics workspace._
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-polyglot:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-dl:latest`
	- Docker Hub Tag: `oaktreetech/root.hub-polyglot`

Dask runtime (Python cluster computing):

* `Dockerfile.k8s-dask`: Dask distributed computing framework and associated libraries.
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-dask:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-k8s-polyglot:latest`
	- Docker Hub Tag: `oaktreetech/root.dask`

Natural language processing (NLP):

* `Dockerfile.k8s-nlp`:
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-nlp:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-dask:latest`
	- Docker Hub Tag: `oaktreetech/root.nlp`

Computer vision and image segmentation:

* `Dockerfile.itk`: Tools for image and volume visualization using the Insight Toolkit (ITK) and Visualization Toolkit (VTK), libraries for mesh analysis such as PyVista, and Sonador 3D. _Used as the advanced computer vision container in the Sonador imaging environment._
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-itk:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-nlp:latest`
	- Docker Hub Tag: `oaktreetech/root.itk`
* `Dockerfile.cv`: Tools and dependencies useful for working on computer vision problems and utilities for working with CAD files such as OpenCascasde and PyOCC-Core.
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-cv:latest`
	- Parent: `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-itk:latest`
* `Dockerfile.3d`: Tools and dependencies useful for working with 3D data.
	- Tagged as `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-3d:latest`
	- Parent `registry.oak-tree.tech/courseware/oak-tree/dataops-examples/spark-cv:latest`
	- Docker Hub Tag: `oaktreetech/root.3d`
