# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

WORKDIR /pipeline

COPY requirements.txt .
COPY *.py ./

# Check to make sure gcc and other segment-geospatial dependencies are there

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  gcc libglib2.0-0 libx11-6 libxext6 libgl1\
  && apt-get autoremove -y \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Install the pipeline requirements and check that there are no conflicts.
# Since the image already has all the dependencies installed,
# there's no need to run with the --requirements_file option.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip check

ADD https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth /root/.cache/torch/hub/checkpoints/sam_vit_h_4b8939.pth
RUN chmod 777 /root/.cache/torch/hub/checkpoints/sam_vit_h_4b8939.pth

# Set the entrypoint to Apache Beam SDK worker launcher.
COPY --from=apache/beam_python3.10_sdk:2.48.0 /opt/apache/beam /opt/apache/beam
ENTRYPOINT [ "/opt/apache/beam/boot" ]