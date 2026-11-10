try:
    await app.change_role_assignments([])
except ValueError as error:
    print(type(error).__name__)
