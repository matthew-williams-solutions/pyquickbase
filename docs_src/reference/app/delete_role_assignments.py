try:
    await app.delete_role_assignments([])
except ValueError as error:
    print(type(error).__name__)
