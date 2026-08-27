from setuptools import setup, find_packages

setup(
    name="analysis-gui",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.8",
    install_requires=[
        "PyQt6>=6.0.0",
        "numpy>=1.20.0",
        "pandas>=1.1.0",
        "matplotlib>=3.3.0",
        "scikit-learn>=0.24.0",
        "tensorflow>=2.10.0",
    ],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-cov>=2.12",
            "black>=21.0",
            "flake8>=3.9",
            "mypy>=0.900",
        ],
        "models": [
            "anthropic>=0.7.0",
            "openai>=1.0.0",
        ],
        "claude": [
            "anthropic>=0.7.0",
        ],
        "gpt": [
            "openai>=1.0.0",
        ],
        "neural": [
            "mne>=1.0.0",
        ],
        "eeg": [
            "mne>=1.0.0",
        ],
        "spike": [
            "spikeinterface>=0.101.0",
        ],
        "s3": [
            "boto3>=1.26.0",
        ],
        "gcs": [
            "google-cloud-storage>=2.10.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "analysis-gui=analysis_gui.main:main",
            "analysis-gui-cli=analysis_gui.cli:main",
        ],
    },
    author="Your Name",
    author_email="your.email@example.com",
    description="A visual interface for data analysis, starting with neural network visualization",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/analysis-gui",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)
