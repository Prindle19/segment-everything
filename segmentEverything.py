''' Copyright 2023 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.'''

# Use Python argparse module to parse custom arguments
import argparse

import os
import fnmatch
import logging
from datetime import datetime

import apache_beam as beam
from apache_beam.io import WriteToText
from apache_beam.options.pipeline_options import PipelineOptions

# Import Google Cloud Storage
from google.cloud import storage

# Configure Logging level
logging.basicConfig(level=logging.INFO)


## Get the URLs of all the Images you want to segment and yield them to the pipeline
def list_blobs(source_bucket, source_folder, file_pattern, output_bucket, output_folder, delimiter=None):
# Find all of the source GeoTIFFs and yield them to the pipeline
	storage_client = storage.Client()

	blobs = storage_client.list_blobs(
		source_bucket, prefix=source_folder, delimiter=delimiter
	)

	for blob in blobs:
		if fnmatch.fnmatch(blob.name, file_pattern):
			yield {'blob_name':blob.name,
       'source_bucket':source_bucket,
       'output_bucket': output_bucket,
       'output_folder': output_folder
       }

## Download and Segment each Image
class segment_it(beam.DoFn):
  # Download a GeoTIFF exported from EE, segment it.
  def process(self, element):
    from google.cloud import storage
    import apache_beam as beam
    from samgeo import SamGeo
    import torch
    import os
    import geopandas as gpd
    import uuid

    bucket_name = element['source_bucket']
    blob_name = element['blob_name']
    storage_client = storage.Client()


    # Get the Earth Engine Exported GeoTIFF
    blob = storage_client.bucket(bucket_name).get_blob(blob_name)
    new_tif_filename = blob_name.split('/')[-1]
    blob.download_to_filename(new_tif_filename)
    logging.info('Image {} was downloaded to {}.'.format(str(blob_name),new_tif_filename))


    logging.info("Processing: " + new_tif_filename)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    logging.info("Segment Anything Hardware Device will be: " + device)
    if device == 'cuda':
      logging.info("CUDA Hardware is: " + torch.cuda.get_device_name(0))

    # Define Segment Anything Keyword Arguments (Parameters)
    sam_kwargs = {
      "points_per_side": 32,
      "pred_iou_thresh": 0.86,
      "stability_score_thresh": 0.92,
      "crop_n_layers": 1,
      "crop_n_points_downscale_factor": 2,
      "min_mask_region_area": 100,
    }
    model_path = '/root/.cache/torch/hub/checkpoints/sam_vit_h_4b8939.pth'
    check_model = os.path.isfile(model_path)
    if check_model:
      logging.info("SAM Model Found at " + model_path)
    else:
      logging.info("SAM Model NOT Found at " + model_path)

    # Define Segment Anything Model, Device, and Keyword Arguments
    sam = SamGeo(
      model_type="vit_h",
      checkpoint='/root/.cache/torch/hub/checkpoints/sam_vit_h_4b8939.pth',
      device=device,
      sam_kwargs=sam_kwargs
    )


    # Generate Masks from Image

    tile_basename = new_tif_filename.split('.tif')[0]

    mask_image_filename = tile_basename + '_mask.tif'
    gpkg_image_filename = tile_basename + '.gpkg'

    logging.info("Mask image will be: " + mask_image_filename)
    try:
      sam.generate(new_tif_filename, output=mask_image_filename, foreground=True, unique=True, batch=False)
    except Exception as e:
      error = str(e)
      logging.error(e)
    logging.info("Masks generated to: " + mask_image_filename)

    # Create a GeoPackage from the Mask GeoTIFF
    sam.tiff_to_vector(mask_image_filename, gpkg_image_filename)
    
    logging.info("GeoPacakge extracted to: " + gpkg_image_filename)

    # Process the GeoPackage to extract the segmented objects and yield to pipeline

    gdf = gpd.read_file(gpkg_image_filename)

    # Add a UUID and tile index identifier to each object

    gdf['tile'] = tile_basename
    gdf['uuid'] = gdf.apply(lambda x: uuid.uuid4(), axis=1)

    # Ensure that the Geometry is WKT
    wkt = gdf.to_wkt()

    # Yield each row to the pipeline on the 'segmented_object' tagged output

    for row in wkt.iterrows():
      row_to_yield = str(row[1]['tile']) + ";" + str(row[1]['uuid']) + ";" + str(int(row[1]['value'])) + ";'" + str(row[1]['geometry']) + "'"

      yield beam.pvalue.TaggedOutput('segmented_object',row_to_yield)


    # Clear GPU Memory used by SAM
    torch.cuda.empty_cache()

    # Upload Mask and GeoPackage to GCS bucket

    output_bucket = element['output_bucket']
    output_folder = element['output_folder']
    if not output_folder:
      gcs_mask_filename = "mask/" + mask_image_filename
      gcs_gpkg_filename = "geopackage/" + gpkg_image_filename
    else:
      gcs_mask_filename = element['output_folder'] + "/" + "mask/" + mask_image_filename
      gcs_gpkg_filename = element['output_folder'] + "/" + "geopackage/" + gpkg_image_filename

    upload_bucket = storage_client.bucket(output_bucket)

    # Upload The Mask GeoTIFF
    new_blob = upload_bucket.blob(gcs_mask_filename)
    new_blob.upload_from_filename(mask_image_filename)
    mask_uri = ('gs://{}/{}'.format(output_bucket,gcs_mask_filename))
    logging.info('Mask GeoTIFF uploaded to: {}'.format(mask_uri))

    # Upload The GeoPackage
    new_blob = upload_bucket.blob(gcs_gpkg_filename)
    new_blob.upload_from_filename(gpkg_image_filename)
    gpkg_uri = ('gs://{}/{}'.format(output_bucket,gcs_gpkg_filename))
    logging.info('Mask GeoTIFF uploaded to: {}'.format(gpkg_uri))


    # Delete the Source GeoTIFF, Mask GeoTIFF, and GeoPacakge
    os.remove(new_tif_filename)
    os.remove(mask_image_filename)
    os.remove(gpkg_image_filename)

    logging.info('Local copies of '+ new_tif_filename + ' and derivatives deleted on worker')


# Configure parseable argumeents

parser = argparse.ArgumentParser()

parser.add_argument(
  '--project',
  required=True,
  help='Specify GCP project where Dataflow pipeline will run and usage will be billed.')
parser.add_argument(
  '--region',
  default='us-central1',
  required=True,
  help='The GCP region in which the pipeline will run.')
parser.add_argument(
  '--worker_zone',
  default='us-east',
  required=False,
  help='The GCP region in which the pipeline will run.')
parser.add_argument(
  '--machine_type',
  default='n1-highmem-2',
  required=True,
  help='The Compute Engine machine type to use for each worker.')
parser.add_argument(
  '--workerDiskType',
  default='workerDiskType',
  required=True,
  help='The type of Persistent Disk to use, specified by a full URL of the disk type resource. For example, use compute.googleapis.com/projects/PROJECT/zones/ZONE/diskTypes/pd-ssd to specify an SSD Persistent Disk. ')
parser.add_argument(
  '--max_num_workers',
  type=int,
  default=4,
  required=False,
  help='The maximum number of workers to scale to in the Dataflow pipeline.')
parser.add_argument(
  '--source_bucket',
  required=True,
  help='GCS Source Bucket ONLY (no folders) e.g. "bucket_name"')
parser.add_argument(
  '--source_folder',
  required=True,
  help='GCS Bucket folder(s) e.g. "folder_level1/folder_level2"')
parser.add_argument(
  '--file_pattern',
  required=True,
  help='File pattern to search e.g. "*.tif"')
parser.add_argument(
  '--output_bucket',
  required=True,
  help='GCS Output Bucket ONLY (no folders) e.g. "bucket_name" for Exports')
parser.add_argument(
  '--output_folder',
  required=True,
  help='GCS Output Bucket folder(s) e.g. "folder_level1/folder_level2" for Outputs - can be blank for root of bucket')
parser.add_argument(
  '--runner',
  default='DataflowRunner',
  required=True,
  help='Run the pipeline locally or via Dataflow')
parser.add_argument(
  '--dataflow_service_options',
  default='worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver',
  required=True,
  help='Configure the GPU Requirement')
parser.add_argument(
  '--experiments',
  default='use_runner_v2,disable_worker_container_image_prepull',
  required=True,
  help='Configure any required Experiments')
parser.add_argument(
  '--number_of_worker_harness_threads',
  default=1,
  required=True,
  help='Controls the maximum number of concurrent units of work that can be assigned to one worker VM at a time.')
parser.add_argument(
  '--sdk_location',
  default='container',
  required=True,
  help='Where to pull the Beam SDK from, you want it to come from the custom container')
parser.add_argument(
  '--disk_size_gb',
  default=100,
  required=True,
  help='The size of the worker harddrive')
parser.add_argument(
  '--sdk_container_image',
  default='gcr.io/cloud-geographers-internal-gee/segment-everything-minimal',
  required=True,
  help='Custom Container Location')


# Parse all arguments
options = parser.parse_args().__dict__
output_bucket = options['output_bucket']
output_folder = options['output_folder']

if not output_folder:
	staging_location = os.path.join('gs://',output_bucket,'tmp', 'staging')
	temp_location = os.path.join('gs://',output_bucket, 'tmp')
else:
	staging_location = os.path.join('gs://',output_bucket + "/" + output_folder, 'tmp', 'staging')
	temp_location = os.path.join('gs://',output_bucket + "/" + output_folder, 'tmp')

logging.info("Staging Location {}".format(staging_location))
logging.info("Temp Location {}".format(temp_location))	


# Correctly type set some of the arguments to what Dataflow is expecting 

now = datetime.now()

time_string = now.strftime("%Y-%m-%d--%H-%M-%S")
job_name = "segment-everything-" + time_string

experiments = options['experiments'].split(',')
dataflow_service_options = options['dataflow_service_options'].split(',')

options.update({'experiments':experiments,
	'dataflow_service_options':dataflow_service_options,
	'disk_size_gb':int(options['disk_size_gb']),
	'staging_location':staging_location,
	'temp_location':temp_location,
	'job_name':job_name,
  'number_of_worker_harness_threads':int(options['number_of_worker_harness_threads']),
	'teardown_policy':'TEARDOWN_ALWAYS'
  })

# Determine output CSV Location
output_bucket = options['output_bucket']
output_folder = options['output_folder']
if not output_folder:
  gcs_csv_filename = "gs://" + output_bucket + "/csv/" + mask_image_filename
else:
  gcs_csv_filename = "gs://" + output_bucket + "/" + output_folder + "/" + "csv/" + time_string

# Construct the Pipeline Options
opts = beam.pipeline.PipelineOptions(flags=[], **options)

# Define the Dataflow Pipeline
p = beam.Pipeline(options['runner'], options=opts)

sources = (
	p | beam.Create(list_blobs(options['source_bucket'],
    options['source_folder'],
    options['file_pattern'],
    options['output_bucket'],
    options['output_folder'])
    )
	)

converted = (
		sources | 'Segment Anything' >> beam.ParDo(segment_it()).with_outputs()
	)

converted.segmented_object | "Create Geometry CSV" >> beam.io.WriteToText(gcs_csv_filename,
  file_name_suffix='.csv',
  header='tile;uuid;value;geometry',
  num_shards=1)

p.run().wait_until_finish()

