from setuptools import setup, find_packages

setup(
    name="notion-mcp-light",
    version="0.1.0",
    description="Notion MCP Light - Notion APIを使用してMarkdownファイルとNotionページを同期するMCPサーバー",
    author="NotionMCP Light Team",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "notion-client",
        "python-mcp-sdk",
        "markdown",
        "mistune",
        "python-dotenv",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Operating System :: OS Independent",
        "License :: OSI Approved :: MIT License",
        "Topic :: Text Processing :: Markup :: Markdown",
        "Topic :: Office/Business :: Office Suites",
    ],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "notion-mcp-light=src.main:main",
        ],
    },
)
