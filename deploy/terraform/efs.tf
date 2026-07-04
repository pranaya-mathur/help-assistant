resource "aws_efs_file_system" "chroma" {
  creation_token = "${local.name_prefix}-efs"
  encrypted      = true

  tags = {
    Name = "${local.name_prefix}-efs"
  }
}

resource "aws_efs_mount_target" "chroma" {
  count = length(aws_subnet.private)

  file_system_id  = aws_efs_file_system.chroma.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "chroma" {
  file_system_id = aws_efs_file_system.chroma.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/chroma-data"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "755"
    }
  }

  tags = {
    Name = "${local.name_prefix}-efs-ap"
  }
}
