This is my project with Comfyui. My workflow is Wan 2.2 Animate where i from reference image (my character) and reference video (target motion and background) generate video with my character.
How i work usually:

1. rent server on VastAi
2. upload setup script into server setup.sh
3. run setup script - it download comfyui repo on server, install all necessary nodes, download all necessary for workflow models: checkpoints/loras, download workflow, run comfyui
4. i connect to comfyui
5. i open my workflow
6. i upload reference image and video
7. click run and wait until workflow will finished
8. download result

Your task is to create automated pipeline starting from 3rd step: automatioon of setup comfyui and running workflows with uploading necessary data and downloading result without manipulating with comfyui - all through cli or python.

So as a result i want to have user friendly cli (not overcomplicated) service which will setup all comfy for concrete workflow: we need to have some mapping workflow <> dependent models,nodes for installation and duirng run we chose which workflow used. then take as input pairs: reference image + video -> run workflow and return result.

It should have some configuration like: which workflow use, and then for all input pairs it will run generation. also we could sequently run several workflows. all data should be saved in output folder with logic names.

also create .venv in which we install all libs and it also will be uploaded to server with codebase and will be used for running all staff.

Note - this is just one automation part - so in src folder make module for this part. then we will add also agent which will rent server and manipuate with it, agent for telegram chatting with user for setuping all this staff.
