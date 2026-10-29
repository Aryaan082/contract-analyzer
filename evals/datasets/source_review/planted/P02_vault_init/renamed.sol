// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface ID1 {
    function ID2(address from, address ID3, uint256 ID4) external returns (bool);
}

contract ID5 {
    address public ID6;
    address public ID7;
    uint256 public ID8;
    mapping(address => uint256) public ID9;

    function ID10(address ID11, address ID12, uint256 ID13) external {
        ID6 = ID11;
        ID7 = ID12;
        ID8 = ID13;
    }

    function ID14(uint256 ID4) external {
        require(ID9[msg.sender] + ID4 <= ID8, "cap reached");
        ID9[msg.sender] += ID4;
        require(ID1(ID7).ID2(msg.sender, address(this), ID4), "pull failed");
    }
}
