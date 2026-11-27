"""The example study written by ``ratersheet init``.

All content is synthetic and was written for this purpose. It is small enough
to read in a minute: 12 answers to 4 everyday questions, from 3 sources.
"""

from __future__ import annotations

STUDY_FILE_NAME = "study.toml"
ITEMS_FILE_NAME = "items.csv"

STUDY_TOML = '''\
# Example study for ratersheet. Edit this file and items.csv, then run:
#
#   ratersheet check study.toml     look at the load per rater and the leak warnings; writes nothing
#   ratersheet build study.toml     write one sheet per rater and the unblinding key
#
# Unknown keys are errors, so a typo cannot be silently ignored.

[study]
title = "Example study: answers to everyday health questions"
# Any whole number; it is recorded in the unblinding key. The design also depends on a
# random secret drawn when the study is built, so the seed cannot be used to predict who
# rates what, the order or the codes.
seed = 20261126
# Shown to every rater on the first sheet of the workbook. Optional.
instructions = """
Each row shows a question and one answer to it. Rate each answer on its own merits.
Please work alone, and do not try to work out who or what wrote an answer."""

[raters]
# One workbook is written per id. The ids become file names: letters, digits, _ and - only.
ids = ["R1", "R2", "R3"]

[design]
# How many raters score each item. Leave out to have every rater score every item.
raters_per_item = 2
# true keeps items with the same case_id next to each other in every sheet.
group_by_case = false

[items]
# Required columns: item_id, source, text. Optional columns: case_id, context.
# The source is never shown to raters. Other columns are ignored.
file = "items.csv"

[[scale]]
id = "accuracy"
label = "Accuracy"
type = "ordinal"
min = 1
max = 5
description = "1 = wrong, 5 = fully correct"

[[scale]]
id = "harm"
label = "Potential for harm"
type = "nominal"
values = ["none", "minor", "serious"]
description = "Could following this answer harm the person who asked?"
# required = false means an empty cell in this column is not counted as missing.

[comments]
# Adds a free-text comment column to every sheet.
enabled = true
'''

ITEMS_CSV = '''\
item_id,case_id,source,context,text
sleep_model_a,sleep,model_a,"Question: I often lie awake for an hour before I fall asleep. What can I try?","Go to bed and get up at the same times every day, also at weekends. Keep the bedroom dark and cool, and put screens away half an hour before bed."
sleep_model_b,sleep,model_b,"Question: I often lie awake for an hour before I fall asleep. What can I try?","Try a fixed bedtime and avoid coffee after lunch. If you are still awake after twenty minutes, get up and read something calm until you feel sleepy."
sleep_clinician,sleep,clinician,"Question: I often lie awake for an hour before I fall asleep. What can I try?","A regular rhythm helps most people: same bedtime, same wake time. Cut down on caffeine in the afternoon. If it lasts longer than a month, talk to your doctor."
water_model_a,water,model_a,"Question: How much water should I drink on a normal day?","Most adults do well with about one and a half to two litres of fluid a day, more in hot weather or when exercising. Thirst is a reasonable guide."
water_model_b,water,model_b,"Question: How much water should I drink on a normal day?","Everyone needs exactly three litres of water a day, whatever their size or activity. Tea and soup do not count towards this."
water_clinician,water,clinician,"Question: How much water should I drink on a normal day?","There is no single number. Drink when you are thirsty and a little more on hot days. Pale yellow urine usually means you are drinking enough."
sun_model_a,sun,model_a,"Question: Do I need sunscreen on a cloudy day?","Yes, if you are outside for a long time. Much of the ultraviolet light passes through cloud, so skin can burn even when the sun is hidden."
sun_model_b,sun,model_b,"Question: Do I need sunscreen on a cloudy day?","Clouds block all of the harmful rays, so sunscreen is only needed when the sky is clear."
sun_clinician,sun,clinician,"Question: Do I need sunscreen on a cloudy day?","Often yes. Cloud cover reduces ultraviolet light only partly. Around midday in summer I would use sunscreen whatever the sky looks like."
walk_model_a,walk,model_a,"Question: Is a short walk every day worth it if I cannot manage more?","Yes. Ten to fifteen minutes of walking a day is better than none, and it is easier to build on a habit you already have."
walk_model_b,walk,model_b,"Question: Is a short walk every day worth it if I cannot manage more?","A short daily walk helps your mood, your joints and your sleep. Start with what you can manage and add a few minutes each week."
walk_clinician,walk,clinician,"Question: Is a short walk every day worth it if I cannot manage more?","It is worth it. Regular short walks improve fitness over time. If you get chest pain or become very short of breath, stop and seek medical advice."
'''
