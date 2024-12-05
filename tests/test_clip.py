# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.cli import start_servers, stop_servers, run_prompt
from gaia.agents.Clip.app import MyAgent


def test_query_engine():
    video_id = "MCi8jgALPYA"
    video_url = [clip.get_video_url(video_id)]
    doc = clip.get_youtube_transcript_doc(video_url)
    doc[0].doc_id = video_id

    clip.build_vector_index(doc)
    clip.build_query_engine()

    query = "What did Lisa Su talk about at Computex 2024?"
    response = clip.query_engine.query(query)
    print(response)


start_servers(enable_agent_server=False)
clip = MyAgent()
test_query_engine()
stop_servers()
