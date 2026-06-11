from setuptools import setup, find_packages

setup(
    name="radacleaner",
    version="0.3.0",
    description="Моніторинг законопроектів ВРУ з LLM-аналізом ризиків",
    author="Leonid Zyryanov",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "python-telegram-bot>=20.7",
        "requests>=2.31.0",
        "PyMuPDF>=1.23.0",
        "python-dotenv>=1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "radacleaner-sync=src.bill_sync:main",
            "radacleaner-monitor=src.rag_engine:main",
        ],
    },
)
