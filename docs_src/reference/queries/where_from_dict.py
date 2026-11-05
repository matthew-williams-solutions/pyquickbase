from pyquickbase import where_from_dict

data = {
    "all": [
        {"field": "Status", "op": "==", "value": "Open"},
        {"field": "Hours", "op": ">", "value": 10},
    ]
}
where = where_from_dict(data, fields=["Status", "Hours"])
print(await tasks.count(where))
