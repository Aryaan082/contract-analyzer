// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    mapping(address => uint256) public ID2;
    uint256 public ID3;

    function ID4(address ID5) external payable {
        ID2[ID5] += msg.value;
        ID3 += msg.value;
    }

    function ID6() external {
        uint256 ID7 = ID2[msg.sender];
        require(ID7 > 0, "nothing owed");
        (bool ID8, ) = msg.sender.call{value: ID7}("<str:0>");
        require(ID8, "send failed");
        ID2[msg.sender] = 0;
        ID3 -= ID7;
    }
}
