"""
setup.py
Dataflow requires a setup.py to package and distribute custom modules
(processing.py in our case) to all worker nodes.
"""
import setuptools

setuptools.setup(
    name="stream-benchmark",
    version="1.0.0",
    description="Stream processing architecture benchmark on GCP",
    install_requires=[
        "apache-beam[gcp]>=2.56.0",
        "google-cloud-pubsub>=2.21.1",
        "google-cloud-bigquery>=3.21.0",
        "google-cloud-storage>=2.16.0",
    ],
    packages=setuptools.find_packages(),
    py_modules=["processing"],
)
