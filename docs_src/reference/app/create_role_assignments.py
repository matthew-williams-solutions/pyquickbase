try:
    await app.create_role_assignments([])
except ValueError as error:
    print(type(error).__name__)
