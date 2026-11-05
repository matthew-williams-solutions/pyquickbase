for column in JobTasks.__columns__:
    if column.label == "Status":
        print(column.name, column.id, column.field_type)
        print(column.writable)
