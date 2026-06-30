ARG OPEN_WEBUI_BASE_IMAGE=ghcr.io/open-webui/open-webui:main
FROM ${OPEN_WEBUI_BASE_IMAGE}

COPY patch_open_webui.py /tmp/patch_open_webui.py

RUN python3 /tmp/patch_open_webui.py && rm /tmp/patch_open_webui.py
