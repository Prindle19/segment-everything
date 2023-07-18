# segmentEverything

This is not an officially supported Google product, though support will be provided on a best-effort basis.

Copyright 2023 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

## Introduction

segmentEverything is a simple python based [Apache Beam](https://beam.apache.org/) pipeline, optimized for [Google Cloud Dataflow](https://cloud.google.com/dataflow), which aims to allow users to provide a [Google Cloud Storage](https://cloud.google.com/storage) bucket of GeoTIFF images exported from [Google Earth Engine] (https://earthengine.google.com/) and produce a vector representation of the areas. in the GeoTIFFs. 

segmentEverything builds heavily upon the awesome work of the [segment-geospatial](https://samgeo.gishub.org/) project which itself draws upon the [segment-anything-eo project](https://github.com/aliaksandr960/segment-anything-eo) and, ultimtately, from Meta's [Segment Anything model](https://github.com/aliaksandr960/segment-anything-eo)

This repository includes both the pipeline script (segmentEverything.py) which calls the pipeline, and, the Dockerfile / build.yaml required to build a [custom container](https://cloud.google.com/dataflow/docs/guides/using-custom-containers) which will be used as the worker by Dataflow. 

The custom container will pre-build all of the required dependencies for segment-geospatial, including pytorch, as it builds from a pytorch-optimized container reference. It also will include all of the required NVIDIA CUDA drivers neccessary to leverage a GPU attached to the worker, and the ~3GB Segment Anything Visual Tranformer Checkpoint (ViT_H). This means that every worker that is created doesn't need to download that checkpoint or build all of the dependencies at run time.

## Preparation

### Build the segment-geospatial-minimal custom container and push to Cloud Container Registry. 

`gcloud builds submit --config build.yaml
`

### Clone this repository and configure a Compute Engine Virtual Machine to launch Dataflow Pipelines
**Note:** Use [tmux](https://github.com/tmux/tmux/wiki) to ensure your pipeline caller session isn't terminated prematurely. 

Choose a [Google Cloud Platform project](https://cloud.google.com/resource-manager/docs/creating-managing-projects) where you will launch this Dataflow pipeline. 

You don't need much of a machine to launch the pipeline as none of the processing of the pipeline is done locally.

1. Start with an Ubuntu 20.04 Image
2. start a tmux session (this will ensure that your pipeline finishes correctly even if you lose connection to your VM). `tmux` 
3. Update aptitude. `sudo apt-get update`
4. Install pip3 `sudo apt-get install python3-pip gcc libglib2.0-0 libx11-6 libxext6 libgl1`
5. Clone this repository `git clone XXXXXX`
6. Change directories into the new "segmentEverything" folder: `cd segmentEverything `
7. Install local python3 dependencies `pip install -r requirements.text`
8. You'll need to authenticate so that the python code that runs locally can use your credentials. `gcloud auth application-default login`

### Enable the Dataflow API  

You need to enable the Dataflow API for your project [here](https://console.developers.google.com/apis/api/dataflow.googleapis.com/overview).

Click "Enable"

![](https://storage.googleapis.com/cogbeam-images/dataflowapi1.png)

## Usage

`python3 cogbeam.py -h`

| Flag              | Required | Default       | Description                                                                                                                        |
|-------------------|----------|---------------|------------------------------------------------------------------------------------------------------------------------------------|
| --project         |True      |               | Specify GCP project where pipeline will run and usage will be billed  
| --region         | True      | us-central1    | The GCP region in which the pipeline will run.
| --worker_zone         | False      | us-central-a    | The GCP zone in which the pipeline will run
| --machine_type         | True      | n1-highmem-2    | The Compute Engine machine type to use for each worker.
| --workerDiskType         | True      | n1-highmem-2    | The type of Persistent Disk to use, specified by a full URL of the disk type resource. For example, use compute.googleapis.com/projects/PROJECT/zones/ZONE/diskTypes/pd-ssd to specify an SSD Persistent Disk. 
| --max_num_workers         | True      | 4    | The maximum number of workers to scale to in the Dataflow pipeline.
| --source_bucket         | True      |     | GCS Source Bucket ONLY (no folders) e.g. "bucket_name"
| --source_folder         | True      |     | GCS Bucket folder(s) e.g. "folder_level1/folder_level2"
| --file_pattern         | True      |     | File pattern to search e.g. "*.tif"
| --output_bucket         | True      |     | GCS Output Bucket ONLY (no folders) e.g. "bucket_name" for Exports
| --output_folder         | True      |     | GCS Output Bucket folder(s) e.g. "folder_level1/folder_level2" for Outputs - can be blank for root of bucket
| --runner         | True      |  DataflowRunner   | Run the pipeline locally or via Dataflow
| --dataflow_service_options         | True      |  worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver   | Configure the GPU Requirement
| --sdk_location         | True      |  container   | Where to pull the Beam SDK from, you want it to come from the custom container
| --disk_size_gb         | True      |  100   | The size of the worker harddrive
| --sdk_container_image         | True      |  gcr.io/cloud-geographers-internal-gee/segment-everything-minimal   | Custom Container Location


## Earth Engine Export Considerations

The Segment Anything model accepts only 8bit RGB imagery, and, running the model is GPU intensive. 

When creating exports from Earth Engine, ensure you're exporting an 8bit image. For example, if you have an ee.Image() named "rgb":

`var eightbitRGB = rgb.unitScale(0, 1).multiply(255).toByte();`

Also, it is recommended to export the pixels at 1024x1024 resolution. This gives enough pixels to have a decent area included in the image, but not too much that it overloads the GPU memory. 

`Export.image.toCloudStorage({image:eightbitRGB,
  description:"My Export Task", bucket:"my-bucket",
  fileNamePrefix: "my-folder/",
  region:iowa,
  scale: 3,
  crs: "EPSG:4326",
  maxPixels: 29073057936,
  fileDimensions: 1024
})`

## Example

python3 segmentEverything.py \
--project [my-project] \
--region [my-region] \
--machine_type n1-highmem-8	 \
--max_num_workers 4 \
--workerDiskType googleapis.com/projects/[my-project]/zones/[my-zone]/diskTypes/pd-ssd \
--source_bucket [my-bucket] \
--source_folder [my-folder] \
--file_pattern *.tif \
--output_bucket [my-output-bucket] \
--output_folder [my-output-folder] \
--runner DataflowRunner \
--dataflow_service_options 'worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver' \
--experiments use_runner_v2,disable_worker_container_image_prepull \
--number_of_worker_harness_threads 1 \
--sdk_location container \
--disk_size_gb 100 \
--sdk_container_image gcr.io/[my-project]/segment-everything-minimal 



