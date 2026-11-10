from pyquickbase import F

done_or_long = (F("Status") == "Done") | (F("Hours") > 10)
print(await tasks.count(done_or_long))
